"""Use the private room so another player cannot intercept the manager hit."""
import sys
sys.argv.append('--private-poke')
import scenarios as s
import importlib.util
sys.path.insert(0,str(s.ROOT/'backend'))
spec=importlib.util.spec_from_file_location('load_geometry',s.ROOT/'loadtest/campus_load.py')
geometry=importlib.util.module_from_spec(spec);spec.loader.exec_module(geometry)
from campus.motion import world

async def walk(u,floor,goal,x,y,private=False):
    deadline=s.time.monotonic()+90
    while s.time.monotonic()<deadline:
        interior=(u.state or {}).get('interior')
        actor=(interior or {}).get('actor',{}) if private else (u.state or {}).get('main',{})
        if actor:
            distance=s.math.hypot(actor['x']-x,actor['y']-y)
            if distance<6:
                await u.ws.send('{"type":"input","keys":[]}');return
            point=floor.toward(goal,actor['x'],actor['y'])
            keys=list(floor.ranked(actor['x'],actor['y'],*point)[0][1])
            await u.ws.send(s.json.dumps({'type':'input','keys':keys}))
            if distance<25:
                await s.asyncio.sleep(.02);await u.ws.send('{"type":"input","keys":[]}')
        await s.asyncio.sleep(.1)
    raise TimeoutError('private-room route did not finish')

async def run():
    roof=geometry.Floor(world('rooftop').data);roof.add_goal('entrance',790,1150)
    room=geometry.Floor(world('mission1').data);room.add_goal('manager',355,195)
    s.fixture('poke_prerequisites');s.fixture('refill')
    metadata=s.json.loads((s.OUT/'metadata.json').read_text())
    users=[s.User(key,i,metadata['users'][i]) for i,key in enumerate(s.json.loads((s.OUT/'sessions.json').read_text()))]
    await s.batch('connect:private_poke',users,lambda u:u.open())
    await s.batch('refill:private_poke',users,lambda u:u.control('pepero_refill',{'objectId':'pepero-lounge2-refill'}))
    await s.batch('travel:private_poke',users,lambda u:u.control('quick_travel',{'destination':'roof-workshop'}))
    await s.batch('walk:room1',users,lambda u:walk(u,roof,'entrance',790,1150))
    await s.batch('enter:room1',users,lambda u:u.control('mission_room_enter',{'room':1}))
    await s.batch('walk:private_manager',users,lambda u:walk(u,room,'manager',355,195,True))
    async def hit(u):
        actor=u.state['interior']['actor']
        keys=list(room.ranked(actor['x'],actor['y'],355,150)[0][1])
        await u.ws.send(s.json.dumps({'type':'input','keys':keys}));await u.ws.send('{"type":"input","keys":[]}')
        result=await u.control('poke',{'context':'classroom','subject':'mission1'})
        assert result['hit'] and not result['blocked']
        assert u.inventory==9,f'expected one biscuit spent, got {u.inventory}'
    await s.batch('poke:eleventh_manager_hit',users,hit)
    await s.batch('close:private_poke',users,lambda u:u.close())
if __name__=='__main__':
    try:
        s.asyncio.run(run())
        if any(row['passed']!=row['users'] for row in s.checks):raise SystemExit(1)
    finally:s.summarize();s.raw.close()
