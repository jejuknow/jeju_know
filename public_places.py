"""Allowlisted public transport data; database rows are never API contracts."""

# Templates need the full decorated model; JSON clients only need these summaries.
CARD_FIELDS = (
    'id', 'name', 'slug', 'category', 'category_slug', 'region', 'region_label',
    'image_url', 'emoji', 'one_line', 'signature_menu', 'detail_features',
    'companions', 'features_public', 'is_pick', 'is_new', 'opening_label',
    'status_label', 'status_tone', 'pick_note',
)
MAP_FIELDS = (
    'id', 'name', 'slug', 'category', 'category_slug', 'region', 'region_label',
    'latitude', 'longitude', 'has_coordinates', 'image_url', 'emoji', 'one_line',
    'signature_menu', 'companions', 'is_pick', 'is_new', 'opening_label',
    'status_label', 'status_tone', 'source_type', 'verification_status', 'pick_note',
)


def summary(place, *, map_view=False):
    result = {key: place.get(key) for key in (MAP_FIELDS if map_view else CARD_FIELDS)}
    # PICK uses the editorial reason when its monthly note is empty.
    result['pick_note'] = (place.get('pick_note') or place.get('reason') or '') if place.get('is_pick') else ''
    if map_view:
        result['classification_terms'] = [
            {key: term[key] for key in ('name', 'type')}
            for term in place.get('classification_terms', [])
        ]
    return result
