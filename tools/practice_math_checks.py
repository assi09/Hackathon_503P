"""Independent development checks for public examples; NEVER imported by agent.py."""
import argparse
import json
import math
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from p2p.runtime import Engine, defaults


def close(a,b):
    if isinstance(b,list):
        return isinstance(a,list) and len(a)==len(b) and all(close(x,y) for x,y in zip(a,b))
    return isinstance(a,(int,float)) and abs(a-b)<1e-7


def attention(spec):
    ids={c['id'].lower():c['id'] for c in spec['controls']}
    matrices={name:ids[name] for name in ['q','k','v']}
    toggle=next(c['id'] for c in spec['controls'] if c['type']=='toggle')
    engine=Engine(spec); results=[]
    for label,q,k,v in [
        ('equal scores',[[1,0],[0,1]],[[1,0],[1,0]],[[2,4],[6,8]]),
        ('dominant match',[[3,0],[0,1]],[[3,0],[0,1]],[[1,-1],[4,2]]),
        ('mixed signs',[[-1,2],[2,-3]],[[1,-2],[-1,1]],[[2,5],[-3,1]])]:
        for scaling in [True,False]:
            raw=[[sum(x*y for x,y in zip(qi,kj)) for kj in k] for qi in q]
            scaled=[[x/(math.sqrt(2) if scaling else 1) for x in row] for row in raw]
            weights=[]
            for row in scaled:
                m=max(row); exp=[math.exp(x-m) for x in row]; weights.append([x/sum(exp) for x in exp])
            output=[[sum(row[j]*v[j][d] for j in range(2)) for d in range(2)] for row in weights]
            actual=engine.run({matrices['q']:q,matrices['k']:k,matrices['v']:v,toggle:scaling})
            panels=[p['values'] for p in actual['panels'] if p['kind']=='matrix']
            for name,target in [('raw scores',raw),('scaled scores',scaled),('weights',weights),('weighted output',output)]:
                passed=any(close(p,target) for p in panels)
                results.append({'case':label,'scaling':scaling,'quantity':name,'passed':passed})
    return results


def entropy(spec):
    vector=next(c['id'] for c in spec['controls'] if c['type']=='vector')
    engine=Engine(spec);results=[]
    for probs,target in [([1,0,0,0],0),([.25]*4,2),([.5,.5,0],1),([.5,.25,.25],1.5)]:
        state={vector:probs}
        for c in spec['controls']:
            if c['type']=='number' and any(w in c['label'].lower() for w in ['outcome','number of']):state[c['id']]=len(probs)
        actual=engine.run(state)
        candidates=[m for m in actual['metrics'] if ('entropy' in m['label'].lower() or m['label'] in ['H','H(p)']) and not any(w in m['label'].lower() for w in ['max','gap','normalized'])]
        if not candidates:raise ValueError('Could not identify entropy metric; inspect output schema')
        results.append({'probabilities':probs,'expected_bits':target,'actual':candidates[0]['value'],
                        'passed':close(candidates[0]['value'],target)})
    return results


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('kind',choices=['attention','entropy']);parser.add_argument('lesson',type=Path)
    args=parser.parse_args();spec=json.loads(args.lesson.read_text());results=globals()[args.kind](spec)
    print(json.dumps(results,indent=2));(args.lesson.parent/'independent-math-checks.json').write_text(json.dumps(results,indent=2))
    sys.exit(0 if all(r['passed'] for r in results) else 1)
