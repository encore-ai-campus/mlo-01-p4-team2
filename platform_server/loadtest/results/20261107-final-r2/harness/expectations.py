"""Predeclared expected outcomes, separate from the actual HTTP response codes."""
VALIDATIONS={
 'typing:over_limit':{'statuses':[400],'fields':{'error_message':'타이핑은 11.11초 안에 완료해야 해요.'}},
 'ws:guide_talk':{'statuses':['rejected'],'fields':{'error_message':'18층 꾸왕이 가까이에서 대화해 주세요.'}},
 'main:game_finish:topping':{'statuses':[200],'fields':{'success':False}},
}
GAME_RESULTS={**{f'{game}:{kind}':'SUCCESS' for game in ('timer','typing','term_match') for kind in ('result','retry')},
 'typing:interrupt':'INTERRUPTED'}
MAIN_GAMES={f'main:game_finish:{game}':{'success':True} for game in ('sql_memory','card_match','coating','wrapping')}
MAIN_GAMES['main:game_finish:kkuwang_touch']={'success':None}

def expectation(action,statuses=None):
 if action in VALIDATIONS:return {'purpose':'validation',**VALIDATIONS[action]}
 fields=MAIN_GAMES.get(action,{})
 if action in GAME_RESULTS:fields={'game_status':GAME_RESULTS[action]}
 return {'purpose':'normal','statuses':list(statuses) if statuses is not None else [200,201],'fields':fields}

def matches(row,spec):
 return (row['status'] in spec['statuses'] and all(key in row and row[key]==value for key,value in spec['fields'].items()))
