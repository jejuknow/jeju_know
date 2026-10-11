# Place navigation (2026-10-11)

Home, `/places` and `/map` share `/api/places` and DB classifications from
`/api/place-taxonomy`. Existing `/api/places` and `/api/categories` responses remain
compatible. Optional added query parameters are `region`, comma-separated
`companion`, comma-separated `detail` (stable category slugs), and `condition=rain`
for the existing rainy-day collection. Details match any selected subtype;
region, top category and every selected companion must all match. Invalid or
inactive details return zero results, never a broader fallback.

The homepage initially exposes region, place category and companion only. Food
and cafe choices reveal optional multiple details. Category changes clear detail
selection. The five-card preview links to the complete `/places` result; all
query conditions survive the list-to-map link.

## Data and migration

Migration `20261011_place_filter_redesign` extends the existing `categories` table
with `type`, `parent_id`, `legacy_aliases`. `place_category_links` is the many-to-many
association table. Existing category IDs, slugs, place fields and raw tags are
preserved. Foreign keys prohibit deleting used classifications and cascade links
only when an administrator deletes the associated place.

`static/place-taxonomy-seed.json` is the one-time seed: 13 food subtypes and the
original 10 preferences, including their existing alias/parent-detail mappings.
Only recognized tags in the corresponding parent category are mapped. Untagged
restaurants remain unclassified; unknown tags and photos are preserved. No
coordinates are invented. After migration, database records drive every UI;
startup does not restore deleted terms or overwrite administrator changes.

Before any startup migration, SQLite online backup creates
`/data/backups/jejuno-before-place-filter-redesign-<UTC timestamp>.db` and checks
integrity. Failure stops startup before migration. Migration runs in one
transaction and is recorded in `schema_migrations`. No production DB replacement,
Volume reset or reseeding is performed.

## Administration

`/admin/categories` groups parent categories, food subtypes and cafe features.
Names and ordering can be changed; slugs remain stable for shared URLs. Existing
global unique-name/slug constraints remain. Renaming details retains their former
names as legacy aliases. Used terms or parents with children cannot be deleted;
deactivation hides them from new selections but preserves existing assignments.

Place forms show only appropriate subtype checkboxes. Inactive assigned terms can
be retained. Server validation rejects mismatched/new inactive assignments. Old
form clients without `taxonomy_present` retain associations and map recognized
legacy tags; modern forms explicitly control associations. Raw legacy tags remain
separately editable. PICK, companions and availability are separate place fields.

Tests use isolated databases, not `/data/jejuno.db`. Run the Python unittest suite
with `JEJUNO_DATA_DIR` set to an isolated directory before importing `app`, and run
`node --test tests/*.test.cjs`. Production deployment must verify backup logs,
integrity/FK checks and original-table comparisons against the pre-change backup.
