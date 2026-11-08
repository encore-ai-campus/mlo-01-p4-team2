"""Actual refill, navigation, held biscuit hits, NPC awards for 100 clients."""
import sys
sys.argv.append('--npc')
import scenarios as s

async def run():
    import importlib.util
    sys.path.insert(0,str(s.ROOT/'backend'))
    spec=importlib.util.spec_from_file_location('load_geometry',s.ROOT/'loadtest/campus_load.py')
    geometry=importlib.util.module_from_spec(spec);spec.loader.exec_module(geometry)
    from campus.motion import world
    floor=geometry.Floor(world('campus').data)
    floor.add_goal('manager',2150,818);floor.add_goal('guide',1250,200)
    metadata=s.json.loads((s.OUT/'metadata.json').read_text())
    users=[s.User(key,i,metadata['users'][i]) for i,key in enumerate(s.json.loads((s.OUT/'sessions.json').read_text()))]
    s.fixture('npc_prerequisites');s.fixture('refill')
    await s.batch('connect:npc',users,lambda u:u.open())
    await s.batch('pepero_refill',users,lambda u:u.control('pepero_refill',{'objectId':'pepero-lounge2-refill'}))
    async def walk(u,x,y):
        deadline=s.time.monotonic()+90
        while s.time.monotonic()<deadline:
            actor=(u.state or {}).get('main',{})
            if (u.state or {}).get('interior'):raise RuntimeError('route entered a classroom')
            distance=s.math.hypot(actor.get('x',0)-x,actor.get('y',0)-y)
            if actor and distance<6:
                await u.ws.send('{"type":"input","keys":[]}');return
            if actor:
                target=floor.toward('manager' if x==2150 else 'guide',actor['x'],actor['y'])
                keys=list(floor.ranked(actor['x'],actor['y'],*target)[0][1])
                await u.ws.send(s.json.dumps({'type':'input','keys':keys}))
                if distance<25:
                    await s.asyncio.sleep(.02)
                    await u.ws.send('{"type":"input","keys":[]}')
            await s.asyncio.sleep(.1)
        raise TimeoutError('navigation did not reach target')
    await s.batch('quick_travel:market',users,lambda u:u.control('quick_travel',{'destination':'campus-market'}))
    await s.batch('walk:manager',users,lambda u:walk(u,2150,818))
    await s.batch('manager_greet',users,lambda u:u.control('manager_greet',{'objectId':'manager-suhyeon'}))
    async def poke(u):
        await u.ws.send(s.json.dumps({'type':'input','keys':['a']}))
        await u.ws.send(s.json.dumps({'type':'input','keys':[]}))
        for _ in range(9):
            result=await u.control('poke',{'context':'world','floor':'campus'})
            assert result['hit'] and not result['blocked'],'unblocked hit required'
            await s.asyncio.sleep(.3)
        assert u.inventory==1,f'server inventory={u.inventory}, expected=1'
    await s.batch('manager_poke_with_inventory',users,poke)
    await s.batch('quick_travel:gallery',users,lambda u:u.control('quick_travel',{'destination':'campus-gallery'}))
    await s.batch('walk:guide',users,lambda u:walk(u,1250,200))
    async def guide(u):
        assert u.inventory==1,f'server inventory={u.inventory}'
        await u.control('guide_talk',{'objectId':'campus-guide-kkuwang'})
    await s.batch('guide_talk_one_remaining',users,guide)
    await s.batch('close:npc',users,lambda u:u.close())

if __name__=='__main__':
    try:
        s.asyncio.run(run())
        if any(row['passed']!=row['users'] for row in s.checks): raise SystemExit(1)
    finally: s.summarize();s.raw.close()
