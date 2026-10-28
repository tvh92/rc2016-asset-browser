"""Pure exact clip selection with a complete source-catalog audit trail."""
from copy import deepcopy

SUPPORTED_STATUS = 'included'
SELECTOR_PREFIXES = {'action', 'name', 'hash', 'label'}


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
    if selector.startswith('label:'):
        # The viewer's clip label: 'animset stem: name' when the model has several animsets, else 'name'.
        stem, sep, name = selector[6:].rpartition(': ')
        animset = str(row.get('animset_path') or '').rsplit('/', 1)[-1].rsplit('.', 1)[0]
        return str(row.get('name', '')) == name and (not sep or animset == stem)
    if selector.startswith('action:'):
        return selector[7:] in _action_names(row)
    if selector.startswith('name:'):
        return str(row.get('name', '')) == selector[5:]
    if selector.startswith('hash:'):
        wanted = selector[5:].lower().removeprefix('0x')
        return _name_hash(row) == wanted
    return selector in _action_names(row) or str(row.get('name', '')) == selector


def resolve_clip_selection(entries, selectors=None, picked=False):
    """Return (selected rows, complete catalog) in original source order.

    Each catalog row preserves all decoder/source fields. `selection_status` is
    orthogonal to decoder `status`, so an unsupported or unrelated row is never
    relabeled as a user omission. With no selectors, all supported rows remain
    selected. Exact duplicate selectors are harmless; selector resolution must
    identify exactly one source row and may only select a supported row.

    ``picked``: the selectors are the clips ticked in the browser's picker, which lists every clip the viewer
    reads. A selector there that matches no row or only a row that is not supported is left out (as a run
    without selectors leaves those rows out), and one that matches several rows with the same clip bytes (an
    animset that repeats another's clip) selects the first. Each such selector is listed in ``skipped_selectors``.
    """
    source = list(entries)
    requested = list(selectors or ())
    selected_indices = set()
    skipped = []
    if requested:
        for raw in requested:
            selector = str(raw)
            if not selector:
                raise ClipSelectionError('Empty clip selector')
            prefix, sep, value = selector.partition(':')
            if sep and prefix in SELECTOR_PREFIXES and not value:
                raise ClipSelectionError('Empty value for '+prefix+' selector')
            matches = [i for i, row in enumerate(source) if _matches(row, selector)]
            if picked:
                supported = [i for i in matches if source[i].get('status') == SUPPORTED_STATUS]
                if len({source[i].get('raw_sha256') for i in supported}) == 1:
                    matches = supported[:1]
                elif not supported:
                    skipped.append(dict(selector=selector, reason='matches no supported source clip' if matches
                                        else 'matches no source clip'))
                    continue
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
            'selection_mode': ('picked' if picked else 'explicit') if requested else 'default_all_supported',
        }
        if skipped:
            audit['selection_provenance']['skipped_selectors'] = skipped
        catalog.append(audit)
        if i in selected_indices:
            selected.append(deepcopy(audit))
    return selected, catalog
