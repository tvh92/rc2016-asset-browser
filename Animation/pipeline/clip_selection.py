"""Pure exact clip selection with a complete source-catalog audit trail."""
from copy import deepcopy

SUPPORTED_STATUS = 'included'
SELECTOR_PREFIXES = {'action', 'name', 'hash'}


class ClipSelectionError(ValueError):
    """A selector is invalid, absent, unsupported, or ambiguous."""


def _action_names(row):
    names = []
    if row.get('action') is not None:
        names.append(str(row['action']))
    names.extend(str(x) for x in row.get('experimental_action_names', ()) if x is not None)
    return tuple(dict.fromkeys(names))


def _name_hash(row):
    value = row.get('name_hash')
    if value is None:
        return None
    return str(value).lower().removeprefix('0x')


def _matches(row, selector):
    if selector.startswith('action:'):
        return selector[7:] in _action_names(row)
    if selector.startswith('name:'):
        return str(row.get('name', '')) == selector[5:]
    if selector.startswith('hash:'):
        wanted = selector[5:].lower().removeprefix('0x')
        return _name_hash(row) == wanted
    return selector in _action_names(row) or str(row.get('name', '')) == selector


def resolve_clip_selection(entries, selectors=None):
    """Return (selected rows, complete catalog) in original source order.

    Each catalog row preserves all decoder/source fields. `selection_status` is
    orthogonal to decoder `status`, so an unsupported or unrelated row is never
    relabeled as a user omission. With no selectors, all supported rows remain
    selected. Exact duplicate selectors are harmless; selector resolution must
    identify exactly one source row and may only select a supported row.
    """
    source = list(entries)
    requested = list(selectors or ())
    selected_indices = set()
    if requested:
        for raw in requested:
            selector = str(raw)
            if not selector:
                raise ClipSelectionError('Empty clip selector')
            prefix, sep, value = selector.partition(':')
            if sep and prefix in SELECTOR_PREFIXES and not value:
                raise ClipSelectionError('Empty value for '+prefix+' selector')
            matches = [i for i, row in enumerate(source) if _matches(row, selector)]
            if not matches:
                raise ClipSelectionError('No source clip matches exact selector: '+selector)
            if len(matches) != 1:
                raise ClipSelectionError('Ambiguous exact selector '+selector+'; matches source rows '+', '.join(str(source[i].get('order', i)) for i in matches))
            i = matches[0]
            if source[i].get('status') != SUPPORTED_STATUS:
                raise ClipSelectionError('Selector resolves to a non-supported source clip: '+selector+' (status='+str(source[i].get('status'))+')')
            selected_indices.add(i)
    else:
        selected_indices = {i for i, row in enumerate(source) if row.get('status') == SUPPORTED_STATUS}

    catalog = []
    selected = []
    for i, row in enumerate(source):
        audit = deepcopy(row)
        audit['selection_status'] = 'included' if i in selected_indices else 'omitted_by_user_selection' if row.get('status') == SUPPORTED_STATUS else row.get('status')
        audit['selection_provenance'] = {
            'source_order': row.get('order', i),
            'source_name': row.get('name'),
            'source_name_hash': row.get('name_hash'),
            'source_raw_sha256': row.get('raw_sha256'),
            'selectors': [str(s) for s in requested if _matches(row, str(s))],
            'selection_mode': 'explicit' if requested else 'default_all_supported',
        }
        catalog.append(audit)
        if i in selected_indices:
            selected.append(deepcopy(audit))
    return selected, catalog
