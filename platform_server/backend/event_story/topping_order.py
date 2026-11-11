"""Deterministic order topping game. Positions are replayed at fixed 50 ms ticks."""
import math
import random

def challenge(seed):
    rng=random.Random(seed)
    drops=[]
    kinds=['strawberry','burnt','almond','sprinkle','burnt','strawberry','almond','burnt']
    for i in range(52):
        spawn=300+i*350
        travel=max(900,1500-(i//13)*200)
        x=rng.randrange(110,691)
        drops.append(dict(id=i,kind=kinds[i%len(kinds)],x=x,startX=max(50,min(750,x+rng.choice([-100,-65,65,100]))),atMs=spawn,travelMs=travel,landTick=math.ceil((spawn+travel)/50)))
    return dict(rulesVersion=4,durationMs=20000,tickMs=50,stepPx=20,catchRadius=44,order={'strawberry':5,'almond':5},burnLimit=3,drops=drops)

def replay(c,proof,elapsed):
    positions=proof.get('positions')
    ticks=c['durationMs']//c['tickMs']
    if not isinstance(positions,list) or not 2<=len(positions)<=ticks+1:return None
    if (len(positions)-1)*c['tickMs']>elapsed+100:return None
    if any(type(x) not in (int,float) or not math.isfinite(x) or not 85<=x<=715 for x in positions):return None
    if positions[0]!=400 or any(abs(b-a)>c['stepPx']+.01 for a,b in zip(positions,positions[1:])):return None
    counts={k:0 for k in c['order']};burnt=0;combo=0;best=0;caught=[]
    by_tick={}
    for d in c['drops']:by_tick.setdefault(d['landTick'],[]).append(d)
    for tick,x in enumerate(positions[1:],1):
        for d in by_tick.get(tick,[]):
            if abs(d['x']-x)>c['catchRadius']:
                if d['kind'] in counts and counts[d['kind']]<c['order'][d['kind']]:combo=0
                continue
            caught.append(d['id'])
            if d['kind']=='burnt':burnt+=1;combo=0
            elif d['kind'] in counts:
                if counts[d['kind']]<c['order'][d['kind']]:combo+=1;best=max(best,combo)
                counts[d['kind']]+=1
        done=all(counts[k]>=v for k,v in c['order'].items())
        if done or burnt>=c['burnLimit'] or tick==ticks:
            if tick!=len(positions)-1:return None
            return dict(success=done and burnt<c['burnLimit'],counts=counts,burnt=burnt,bestCombo=best,caught=caught,playedMs=tick*c['tickMs'])
    return None
