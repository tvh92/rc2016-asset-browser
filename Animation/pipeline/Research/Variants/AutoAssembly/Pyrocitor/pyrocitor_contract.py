"""Exact source section visibility contract."""
EXPECTED={"V1":{"main":{0,1,2,3},"v1":{0},"v5":set()},"V5":{"main":{0,1,2,3},"v1":set(),"v5":{0,1,2,3,4}}}
def assert_visible_source_sections(state,actual):
    if state not in EXPECTED: raise AssertionError(f'Unknown state: {state}')
    got={k:set(actual.get(k,())) for k in ('main','v1','v5')}
    if got!=EXPECTED[state]: raise AssertionError(f'{state}: expected {EXPECTED[state]}, got {got}')
def negative_completeness_control():
    try: assert_visible_source_sections('V5',{'main':{0,1,2},'v1':set(),'v5':{0,1,2,3,4}})
    except AssertionError: return True
    raise AssertionError('Incomplete main negative control accepted')
