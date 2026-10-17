"""Validated uploads, atomic content/metadata writes, and reference-aware file cleanup."""
import io
import logging
import re
import threading
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException
from auth_security import safe_external_url
from image_limits import MAX_FILE_BYTES, MAX_PIXELS, MAX_EDGE, FORM_BYTES, BODY_LIMIT_MESSAGE

FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP",
           ".heic": "HEIF", ".heif": "HEIF"}
MIME_TYPES = {"JPEG": {"image/jpeg", "image/jpg"}, "PNG": {"image/png"}, "WEBP": {"image/webp"},
              "HEIF": {"image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence"}}
# Keep libheif/Pillow security limits. Ignore depth/auxiliary images and decode serially.
register_heif_opener(decode_threads=1, depth_images=False, aux_images=False)
KINDS = {"places": "place_id", "issues": "issue_id"}
FILE_PATH = re.compile(r"(?:places|issues)/[0-9a-f]{32}\.webp\Z")
PROCESS_SLOTS = threading.BoundedSemaphore(1)
logger = logging.getLogger(__name__)


class ImageError(ValueError):
    pass


class ClosedFormRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def handle(request):
            try:
                return await handler(request)
            except MultiPartException as exc:
                if exc.message == BODY_LIMIT_MESSAGE:
                    return Response(BODY_LIMIT_MESSAGE, status_code=413)
                raise
            except HTTPException as exc:
                if exc.status_code == 400 and exc.detail == BODY_LIMIT_MESSAGE:
                    return Response(BODY_LIMIT_MESSAGE, status_code=413)
                raise
            finally:
                await request.close()
        return handle


def optimize_image(raw, filename, content_type):
    with PROCESS_SLOTS:
        return _optimize_image(raw, filename, content_type)


def optimize_upload(stream, filename, content_type):
    # Acquire before reading: concurrent requests do not hold multiple original files in RAM.
    with PROCESS_SLOTS:
        return _optimize_image(stream.read(MAX_FILE_BYTES + 1), filename, content_type)


def _optimize_image(raw, filename, content_type):
    expected = None
    dimensions = None
    try:
        # Mobile pickers can transcode HEIC to JPEG without updating name/MIME.
        # Decode only our format allowlist; names and MIME are hints, never proof.
        if content_type and not (content_type.lower().startswith("image/") or content_type.lower() == "application/octet-stream"):
            raise ImageError("지원하지 않는 이미지 형식입니다. JPG, PNG, WebP, HEIC 사진을 선택해주세요.")
        if not raw:
            raise ImageError("사진 파일이 비어 있거나 손상되어 있습니다.")
        if len(raw) > MAX_FILE_BYTES:
            raise ImageError("파일이 너무 큽니다. 사진은 한 장당 32MB까지 처리할 수 있어요.")
        with Image.open(io.BytesIO(raw), formats=list(MIME_TYPES)) as check:
            dimensions = check.size
            expected = check.format
            if check.width * check.height > MAX_PIXELS:
                raise ImageError("사진 크기가 처리 가능한 범위를 넘었습니다. 5,000만 화소 이하의 사진을 선택해주세요.")
            if expected != "HEIF" and getattr(check, "is_animated", False):
                raise ImageError("움직이는 이미지는 지원하지 않습니다. 정지 이미지로 선택해주세요.")
            check.verify()
        with Image.open(io.BytesIO(raw), formats=list(MIME_TYPES)) as source:
            # JPEG decoder scaling / compatible HEIF thumbnails reduce peak memory.
            # A square resize bound commutes with EXIF rotation; rotate only the small image.
            source.draft(None, (MAX_EDGE, MAX_EDGE))
            source.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
            ImageOps.exif_transpose(source, in_place=True)
            with source.convert("RGBA" if "A" in source.getbands() or "transparency" in source.info else "RGB") as pixels:
                # Copy pixels into a fresh image: EXIF/GPS/XMP and profiles never leave the server.
                with Image.new(pixels.mode, pixels.size) as clean:
                    clean.paste(pixels)
                    result = io.BytesIO()
                    clean.save(result, "WEBP", quality=86, method=4, exif=b"", icc_profile=None)
                    logger.info("Image optimized format=%s bytes=%d dimensions=%s output_bytes=%d output=%sx%s", expected, len(raw), dimensions, result.tell(), clean.width, clean.height)
                    return result.getvalue(), clean.width, clean.height
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, RuntimeError, MemoryError, Image.DecompressionBombError) as error:
        # Never log filenames, EXIF, GPS, credentials or the request body.
        logger.warning("Image processing failed format=%s bytes=%d dimensions=%s error=%s reason=%s", expected, len(raw), dimensions, type(error).__name__, str(error) if isinstance(error, ImageError) else "decoder failure", exc_info=not isinstance(error, ImageError))
        if isinstance(error, ImageError):
            raise
        if isinstance(error, UnidentifiedImageError):
            known_header = raw.startswith((b'\xff\xd8', b'\x89PNG', b'RIFF')) or (len(raw) >= 12 and raw[4:8] == b'ftyp')
            message = "사진 파일이 손상되어 있습니다." if known_header else "지원되지 않는 이미지입니다. JPG, PNG, WebP, HEIC 사진을 선택해주세요."
        elif isinstance(error, MemoryError):
            message = "사진을 처리하지 못했습니다. 잠시 후 다시 시도해주세요."
        else:
            message = "사진 파일이 손상되어 있어 처리하지 못했습니다."
        raise ImageError(message) from error


class ImageStore:
    def __init__(self, root, connect_db):
        self.root = Path(root).resolve()
        self.connect_db = connect_db
        for kind in KINDS:
            folder = self.root / kind
            if folder.is_symlink():
                raise RuntimeError("Upload directories must not be symlinks")
            folder.mkdir(parents=True, exist_ok=True)

    def path(self, relative):
        if not FILE_PATH.fullmatch(relative):
            raise ImageError("올바르지 않은 이미지 경로입니다.")
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or path.parent != self.root / relative.split('/')[0]:
            raise ImageError("올바르지 않은 이미지 경로입니다.")
        return path

    def list(self, kind, owner_id):
        with closing(self.connect_db()) as conn:
            rows = conn.execute(f"SELECT * FROM images WHERE {KINDS[kind]}=? ORDER BY sort_order", (owner_id,)).fetchall()
        return [{**dict(row), "url": "/uploads/" + row["file_path"], "is_primary": row["sort_order"] == 0} for row in rows]

    def decorate(self, item, kind):
        item["legacy_image_url"] = item.get("image_url", "")
        item["images"] = self.list(kind, item["id"])
        if kind == "places":
            with closing(self.connect_db()) as conn:
                merged = conn.execute("SELECT i.* FROM images i JOIN places p ON p.id=i.place_id WHERE p.merged_into_id=? ORDER BY p.id,i.sort_order", (item["id"],)).fetchall()
                legacy = conn.execute("SELECT image_url FROM places WHERE merged_into_id=? AND image_url!='' ORDER BY id", (item["id"],)).fetchall()
            # Keep overflow photos and their original records when merging two five-photo places.
            item["merged_images"] = [{**dict(r), "url": "/uploads/" + r["file_path"], "sort_order": 5+n, "is_primary": False} for n,r in enumerate(merged)]
            for row in legacy:
                if safe_external_url(row["image_url"]):
                    item["merged_images"].append({"url": row["image_url"], "is_primary": False})
        else:
            item["merged_images"] = []
        primary = next((image for image in item["images"] if image["is_primary"]), None)
        if primary:
            item["image_url"] = primary["url"]
        elif not item.get("image_url") and item["merged_images"]:
            item["image_url"] = item["merged_images"][0]["url"]
        return item

    async def prepare(self, form, kind, *, slots=None):
        plan = ImagePlan(self, kind)
        count = slots if slots is not None else (5 if kind == "places" else 1)
        uploads = [(key, value) for key, value in form.multi_items() if isinstance(value, UploadFile) and value.filename]
        valid_names = {f"image_{slot}" for slot in range(count)}
        if len(uploads) > count or len({key for key, _ in uploads}) != len(uploads) or any(key not in valid_names for key, _ in uploads):
            raise ImageError(f"사진은 최대 {count}장까지 가능합니다.")
        try:
            for slot in range(count):
                if form.get(f"remove_image_{slot}") == "1":
                    plan.remove.add(slot)
                upload = form.get(f"image_{slot}")
                if not isinstance(upload, UploadFile) or not upload.filename:
                    continue
                output, width, height = await run_in_threadpool(optimize_upload, upload.file, upload.filename, upload.content_type or "")
                relative = f"{kind}/{uuid.uuid4().hex}.webp"
                plan.new[slot] = {"file_path": relative, "width": width, "height": height, "byte_size": len(output)}
                await run_in_threadpool(self.write, relative, output)
            return plan
        except OSError as exc:
            plan.discard()
            logger.exception("Image storage failed error=%s", type(exc).__name__)
            raise ImageError("사진 업로드 중 문제가 발생했습니다. 다시 시도해주세요.") from exc
        except BaseException:
            plan.discard()
            raise
        finally:
            for _, upload in uploads:
                await upload.close()

    def write(self, relative, data):
        with self.path(relative).open("xb") as target:
            target.write(data)

    def collect(self):
        with closing(self.connect_db()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            for row in conn.execute("SELECT file_path FROM image_file_gc").fetchall():
                relative = row[0]
                if conn.execute("SELECT 1 FROM images WHERE file_path=?", (relative,)).fetchone():
                    continue
                # An editor may have reused an uploaded file through the legacy URL field.
                if any(conn.execute(f"SELECT 1 FROM {table} WHERE instr(image_url,?)>0", ('/uploads/' + relative,)).fetchone()
                       for table in ("places", "news_posts")):
                    continue
                try:
                    self.path(relative).unlink(missing_ok=True)
                except (OSError, ImageError):
                    logger.warning("Image cleanup deferred")
                    continue
                conn.execute("DELETE FROM image_file_gc WHERE file_path=?", (relative,))

    def response(self, request, kind, filename):
        if kind not in KINDS:
            return Response(status_code=404)
        try:
            path = self.path(f"{kind}/{filename}")
        except ImageError:
            return Response(status_code=404)
        with closing(self.connect_db()) as conn:
            # Only registered images are served; private/deleted content remains protected.
            rows = conn.execute("""SELECT CASE WHEN COALESCE(c.deleted_at,p.deleted_at) IS NULL THEN COALESCE(c.published,p.published) ELSE 0 END AS place_public,
                COALESCE(c.submitted_by_user_id,p.submitted_by_user_id) AS place_author,
                COALESCE(c.deleted_at,p.deleted_at) AS place_deleted,n.published AS issue_public,
                n.author_id,n.deleted_at FROM images i
                LEFT JOIN places p ON p.id=i.place_id LEFT JOIN places c ON c.id=p.merged_into_id
                LEFT JOIN news_posts n ON n.id=i.issue_id
                WHERE i.file_path=?""", (f"{kind}/{filename}",)).fetchall()
        member = request.scope.get("member")
        allowed = any(request.session.get("admin_id") or (member and member["role"] == "admin") or row["place_public"] or
                      (member and row["place_deleted"] is None and row["place_author"] == member["id"]) or
                      (row["deleted_at"] is None and (row["issue_public"] or
                       (member and member["id"] == row["author_id"]))) for row in rows)
        if not allowed or not path.is_file():
            return Response(status_code=404)
        return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


class ImagePlan:
    def __init__(self, store, kind):
        self.store, self.kind = store, kind
        self.new, self.remove = {}, set()

    def discard(self):
        for image in self.new.values():
            self.store.path(image["file_path"]).unlink(missing_ok=True)

    def delete_owner(self, conn, owner_id):
        column = KINDS[self.kind]
        conn.execute(f"INSERT OR IGNORE INTO image_file_gc SELECT file_path FROM images WHERE {column}=?", (owner_id,))
        conn.execute(f"DELETE FROM images WHERE {column}=?", (owner_id,))

    def apply(self, conn, owner_id):
        column = KINDS[self.kind]
        for slot in self.remove | self.new.keys():
            conn.execute(f"INSERT OR IGNORE INTO image_file_gc SELECT file_path FROM images WHERE {column}=? AND sort_order=?", (owner_id, slot))
            conn.execute(f"DELETE FROM images WHERE {column}=? AND sort_order=?", (owner_id, slot))
            if image := self.new.get(slot):
                conn.execute(f"INSERT INTO images({column},file_path,width,height,byte_size,sort_order) VALUES (?,?,?,?,?,?)",
                             (owner_id, image["file_path"], image["width"], image["height"], image["byte_size"], slot))

    @contextmanager
    def transaction(self):
        try:
            with closing(self.store.connect_db()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
        except BaseException:
            self.discard()
            raise
        self.store.collect()
