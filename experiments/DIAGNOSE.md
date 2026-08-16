# EXP1 Arm-C Underperformance — Full Diagnostic

Run each code block on GPU2 from `~/Dev/snodo-public`.

---

## Run 1: Wilson CI + McNemar + per-repo + divergence + closure health

```bash
python3 -c "
import json, collections, math
rows=[json.loads(l) for l in open('experiments/results/exp1/results.jsonl') if l.strip()]
arms=('a','b','c')

def wilson(k,n):
    if n==0: return (0,0)
    z=1.96; p=k/n
    denom=1+z*z/n
    center=(p+z*z/(2*n))/denom
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/denom
    return (max(0,center-half), min(1,center+half))

def mcnemar_exact(b,c):
    n=b+c
    if n==0: return 1.0
    from math import comb
    k=min(b,c)
    tail=sum(comb(n,i) for i in range(k+1))/(2**n)
    return min(1.0,2*tail)

res=collections.defaultdict(dict)
repo={}
for r in rows:
    a=r.get('arm')
    if a in arms:
        iid=r['instance_id']
        res[iid][a]=res[iid].get(a,False) or bool(r['resolved'])
        repo[iid]=r.get('repo','?')
tasks=sorted(res)

print(f'=== EXP1 analysis ===')
print(f'tasks with all 3 arms: {sum(1 for t in tasks if len(res[t])==3)}/{len(tasks)}')
excl=sum(1 for r in rows if r.get('exclusion_reason'))
errs=collections.Counter(str(r.get('error'))[:30] for r in rows if r.get('arm') in arms and r.get('error'))
print(f'excluded rows: {excl} | error rows: {sum(errs.values())} {dict(errs)}\n')

print('--- resolve rate per arm (Wilson 95% CI) ---')
for a in arms:
    k=sum(1 for t in tasks if res[t].get(a))
    n=sum(1 for t in tasks if a in res[t])
    lo,hi=wilson(k,n)
    print(f'  arm {a}: {k}/{n} = {k/n*100 if n else 0:.1f}%  [{lo*100:.1f}%, {hi*100:.1f}%]')

print('\n--- paired McNemar (exact, two-sided) ---')
for x,y in (('a','c'),('b','c'),('a','b')):
    both=[t for t in tasks if x in res[t] and y in res[t]]
    b=sum(1 for t in both if res[t][x] and not res[t][y])
    c=sum(1 for t in both if res[t][y] and not res[t][x])
    p=mcnemar_exact(b,c)
    print(f'  {x} vs {y}: {x}-only={b} {y}-only={c} concordant={len(both)-b-c} p={p:.4f}')

print('\n--- divergence (arms disagree) ---')
div=[t for t in tasks if len(set(res[t].get(a) for a in arms if a in res[t]))>1]
print(f'{len(div)} tasks differ across arms')
for t in div:
    print(f'  {t:42s} a={int(res[t].get(\"a\",0))} b={int(res[t].get(\"b\",0))} c={int(res[t].get(\"c\",0))}')

print('\n--- arm-c enforcement health ---')
ch=collections.Counter()
att=collections.Counter()
for r in rows:
    if r.get('arm')=='c':
        cj=r.get('closure_json') or {}
        ch[cj.get('outcome')]+=1
        att[cj.get('attempts_used')]+=1
print('closure outcomes:', dict(ch))
print('attempts used:', dict(att))
"
```

---

## Run 2: Full diagnostic dump (sections 1-8)

```bash
python3 -c "
import json
from collections import Counter, defaultdict
rows=[json.loads(l) for l in open('experiments/results/exp1/results.jsonl') if l.strip()]
arms=('a','b','c')

print('=== 1. SUMMARY ===')
for arm in arms:
    rr=[r for r in rows if r['arm']==arm]
    ok=sum(1 for r in rr if r.get('resolved'))
    print(f'Arm {arm}: {ok}/{len(rr)} = {ok/len(rr)*100:.1f}%')

res=defaultdict(lambda:{})
for r in rows:
    a=r.get('arm','')
    if a in arms:
        res[r['instance_id']][a]=res[r['instance_id']].get(a,False) or r.get('resolved',False)
tasks=sorted(res)

print('\n=== 2. A RESOLVED, C DID NOT ===')
a_c=[(t,res[t]) for t in tasks if res[t].get('a') and not res[t].get('c')]
print(f'Count: {len(a_c)}')
for t,d in a_c:
    print(f'  {t:42s} a=1 b={int(d.get(\"b\",0))} c=0')

print('\n=== 3. C RESOLVED, A DID NOT ===')
c_a=[(t,res[t]) for t in tasks if res[t].get('c') and not res[t].get('a')]
print(f'Count: {len(c_a)}')
for t,d in c_a:
    print(f'  {t:42s} a=0 b={int(d.get(\"b\",0))} c=1')

print('\n=== 4. PATTERN COUNTS ===')
pats=Counter()
for t in tasks:
    d=res[t]
    p=f'a={int(d.get(\"a\",0))} b={int(d.get(\"b\",0))} c={int(d.get(\"c\",0))}'
    pats[p]+=1
for p,n in pats.most_common():
    print(f'  {p}: {n}')

print('\n=== 5. ARM C ERRORS ===')
c_rows=[r for r in rows if r.get('arm')=='c']
c_errs=sum(1 for r in c_rows if r.get('error'))
print(f'Rows with errors: {c_errs}/{len(c_rows)}')
errs=Counter(r.get('error','')[:120] for r in c_rows if r.get('error'))
for e,n in errs.most_common(10):
    print(f'  {n}: {e}')

print('\n=== 6. ARM C CLOSURE OUTCOMES ===')
cj=Counter()
for r in c_rows:
    d=r.get('closure_json') or {}
    cj[d.get('outcome','no_closure')]+=1
for k,v in cj.most_common():
    print(f'  {k}: {v}')

print('\n=== 7. ARM C ATTEMPTS USED ===')
att=Counter(d.get('attempts_used','?') for r in c_rows for d in [r.get('closure_json') or {}])
for k,v in att.most_common():
    print(f'  {k} attempts: {v}')

print('\n=== 8. WALL TIME BY ARM ===')
for arm in arms:
    times=sorted([r['wall_s'] for r in rows if r['arm']==arm and not r.get('error') and r.get('wall_s',0)>0])
    if times:
        n=len(times)
        print(f'Arm {arm}: n={n} mean={sum(times)/n:.0f}s median={times[n//2]:.0f}s p90={times[int(n*0.9)]:.0f}s max={times[-1]:.0f}s')
"
```

---

## Run 3: Classify every divergent task (C-win and C-loss)

```bash
python3 -c "
import json
from collections import defaultdict
rows=[json.loads(l) for l in open('experiments/results/exp1/results.jsonl') if l.strip()]
arms=('a','b','c')

res=defaultdict(lambda:{})
for r in rows:
    a=r.get('arm','')
    if a in arms:
        res[r['instance_id']][a]=res[r['instance_id']].get(a,False) or r.get('resolved',False)
tasks=sorted(res)

print('=== C-LOSSES: A resolved, C did not (10 tasks) ===\n')
for t,d in [(t,res[t]) for t in tasks if res[t].get('a') and not res[t].get('c')]:
    c_patch=''; c_err=''; c_wall=0; cj={}
    a_patch_len=b_patch_len=0
    for r in rows:
        if r.get('instance_id')==t and r.get('arm')=='c':
            c_patch=r.get('patch') or ''
            c_err=r.get('error') or ''
            c_wall=r.get('wall_s',0)
            cj=r.get('closure_json') or {}
        if r.get('instance_id')==t and r.get('arm')=='a':
            a_patch_len=len(r.get('patch') or '')
        if r.get('instance_id')==t and r.get('arm')=='b':
            b_patch_len=len(r.get('patch') or '')

    cls='PROMPT_DIFF: OpenCodeAdapter produced wrong answer'
    if len(c_patch)==0:
        cls='BUG: empty patch (closue says resolved)'
    elif c_err and 'timeout' in c_err.lower():
        cls='BUG: timeout'
    elif c_err and ('graph build failed' in c_err or 'closure failed' in c_err):
        cls='BUG: engine error'

    print(f'{t}')
    print(f'  a=1 b={int(d.get(\"b\",0))} c=0')
    print(f'  closure: outcome={cj.get(\"outcome\",\"?\")} attempts={cj.get(\"attempts_used\",\"?\")}')
    print(f'  c_error: {c_err[:100] if c_err else \"none\"}')
    print(f'  c_wall: {c_wall:.0f}s')
    print(f'  patch_len: a={a_patch_len} b={b_patch_len} c={len(c_patch)}')
    print(f'  CLASSIFICATION: {cls}')
    print()

print('=== C-WINS: C resolved, A did not (9 tasks) ===\n')
for t,d in [(t,res[t]) for t in tasks if res[t].get('c') and not res[t].get('a')]:
    a_ok=d.get('a',False)
    b_ok=d.get('b',False)
    a_patch_len=b_patch_len=c_patch_len=0
    a_err=b_err=''
    a_wall=b_wall=0
    for r in rows:
        if r.get('instance_id')==t and r.get('arm')=='a':
            a_patch_len=len(r.get('patch') or ''); a_err=r.get('error') or ''; a_wall=r.get('wall_s',0)
        if r.get('instance_id')==t and r.get('arm')=='b':
            b_patch_len=len(r.get('patch') or ''); b_err=r.get('error') or ''; b_wall=r.get('wall_s',0)
        if r.get('instance_id')==t and r.get('arm')=='c':
            c_patch_len=len(r.get('patch') or '')

    cls=''
    if not a_ok and not b_ok:
        cls='GENUINE sole C win'
    elif a_ok and not b_ok:
        if 'timeout' in b_err.lower():
            cls='SHARED: A also won, B timed out'
        elif b_patch_len==0:
            cls='SHARED: A also won, B empty patch'
        elif b_patch_len>50000:
            cls=f'SHARED: A also won, B hallucinated ({b_patch_len} chars)'
        else:
            cls='SHARED: A also won, B unexplaine failure'
    elif b_ok and not a_ok:
        if 'timeout' in a_err.lower():
            cls='SHARED: B also won, A timed out'
        elif a_patch_len==0:
            cls='SHARED: B also won, A empty patch'
        else:
            cls='SHARED: B also won, A unexplaine failure'

    print(f'{t}')
    print(f'  a=0 b={int(b_ok)} c=1')
    print(f'  A: patch_len={a_patch_len} wall={a_wall:.0f}s error={a_err[:60] if a_err else \"-\"}')
    print(f'  B: patch_len={b_patch_len} wall={b_wall:.0f}s error={b_err[:60] if b_err else \"-\"}')
    print(f'  C: patch_len={c_patch_len}')
    print(f'  CLASSIFICATION: {cls}')
    print()
"
```

---

## Run 4: Patch diffs — side by side for the 10 C-losses

```bash
python3 -c "
import json
rows=[json.loads(l) for l in open('experiments/results/exp1/results.jsonl') if l.strip()]

# Compute all A-wins C-losses
from collections import defaultdict
res=defaultdict(lambda:{})
for r in rows:
    a=r.get('arm','')
    if a in ('a','c'):
        res[r['instance_id']][a]=res[r['instance_id']].get(a,False) or r.get('resolved',False)
a_wins_c_loses=sorted([t for t,d in res.items() if d.get('a') and not d.get('c')])

for t in a_wins_c_loses:
    print(f'\\n========== {t} ==========')
    for arm_label in ('a','c'):
        for r in rows:
            if r.get('instance_id')==t and r.get('arm')==arm_label:
                patch=r.get('patch','')
                print(f'--- Arm {arm_label} (resolved={r.get(\"resolved\",False)} len={len(patch)}) ---')
                # Print first 40 lines
                plines=patch.split(chr(10))
                for i,line in enumerate(plines):
                    if i<40:
                        print(line)
                if len(plines)>40:
                    print(f'... ({len(plines)-40} more lines)')
                print()
                break
" | head -2000
```

---

## Run 5: Audit log for a C-loss task (`django__django-13121`)

```bash
python3 -c "
import json
rows=[json.loads(l) for l in open('experiments/results/exp1/results.jsonl') if l.strip()]
for r in rows:
    if r['instance_id']=='django__django-13121' and r['arm']=='c':
        print(f'trial={r[\"trial_id\"]} resolved={r[\"resolved\"]} run_id={r[\"run_id\"]}')
        cj=r.get('closure_json') or {}
        print(f'closure: outcome={cj.get(\"outcome\")} attempts={cj.get(\"attempts_used\")}')
        audit='experiments/results/exp1/runs/'+r['run_id']+'/arm-c-audit.log'
        print(f'\\naudit log ({audit}):')
        import os
        if os.path.exists(audit):
            for line in open(audit):
                if r['instance_id'] in line:
                    print('  '+line.rstrip())
        else:
            print('  NOT FOUND')
        break
"
```

---

## Run 6: Check for tasks where ALL THREE ARMS time out

```bash
python3 -c "
import json
from collections import defaultdict
rows=[json.loads(l) for l in open('experiments/results/exp1/results.jsonl') if l.strip()]
for arm in ('a','b','c'):
    to=[r for r in rows if r['arm']==arm and r.get('error','')=='timeout']
    if to:
        tids=set(r['instance_id'] for r in to)
        for tid in sorted(tids):
            other=[r['arm'] for r in rows if r['instance_id']==tid and r.get('error','')=='timeout']
            print(f'{tid}: {arm} timeout (other arms also timed out: {[x for x in other if x!=arm]})')
"
```

---

## Run 7: The only genuine C-sole-win — what happened?

```bash
python3 -c "
import json
rows=[json.loads(l) for l in open('experiments/results/exp1/results.jsonl') if l.strip()]
# astropy__astropy-14598: a=0 b=0 c=1
for t in ['astropy__astropy-14598']:
    for r in rows:
        if r['instance_id']==t:
            patch=r.get('patch','')
            print(f'Arm {r[\"arm\"]}: resolved={r.get(\"resolved\")} error={r.get(\"error\",\"-\")[:80]} patch_len={len(patch)} wall={r.get(\"wall_s\",0):.0f}s')
"
```
