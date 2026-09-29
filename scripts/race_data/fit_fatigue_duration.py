import sys, pickle, itertools, statistics
sys.path.insert(0,'/home/user/paceforge/scripts/race_data'); sys.path.insert(0,'/home/user/paceforge')
import dataset, calibrate as cal
import app.services.race_simulator as rs
S=__import__("os").environ.get("RACE_PREPS_DIR", "/tmp/")
old=pickle.load(open(S+"preps.pkl","rb")); ids={r["id"] for r,_ in old}
starts={r["id"]:r["start"] for r in dataset.races(("utmb",)) if r["id"] in ids}
preps=[(dict(r, start=starts.get(r["id"], r["start"])), p) for r,p in old]
print("local start hours:", sorted({round(cal.start_hour(r),1) for r,_ in preps}))
train=[x for i,x in enumerate(preps) if i%2==0]; test=[x for i,x in enumerate(preps) if i%2==1]
orig_night=rs._night_penalty
def set_night(n1,n2):
    def night(cum_s, start_hour=6):
        h=(start_hour+cum_s/3600)%24; e=cum_s/3600
        if 21<=h or h<6: return n1*(n2 if e>20 else 1.0)
        if 20<=h<21 or 6<=h<7: return 1+(n1-1)*0.4
        return 1.0
    rs._night_penalty=night
def dur(c,q,fresh):
    def f(progress,total_km,gain,hours): return 1 + c*(hours/10)**q - fresh*max(0.0,1-hours/3)
    return f
def total_gap(sample,fat):
    agg={}
    for race,p in sample:
        for g,v in cal.score_race(race,p,fat).items(): agg.setdefault(g,[]).append((v[0],v[1]))
    n=sum(a for l in agg.values() for a,_ in l)
    return sum(a*b for l in agg.values() for a,b in l)/n, {g:round(sum(a*b for a,b in l)/sum(a for a,_ in l),1) for g,l in agg.items()}
set_night(1.08,1.0)
print("current fatigue, local night: test", [round(x,1) if isinstance(x,float) else x for x in total_gap(test,cal.current_fatigue)])
best=None
for c,q,fr,n1,n2 in itertools.product([0.2,0.3,0.4],[0.75,1.0,1.5],[0.05,0.1,0.15],[1.0,1.08,1.15],[1.0,1.1,1.2]):
    set_night(n1,n2); m,_=total_gap(train,dur(c,q,fr))
    if best is None or m<best[0]: best=(m,c,q,fr,n1,n2)
m,c,q,fr,n1,n2=best; set_night(n1,n2); te=total_gap(test,dur(c,q,fr))
print(f"best: c={c} q={q} fresh={fr} night={n1} 2nd-night x{n2}: train {round(m,1)} test {round(te[0],1)}", te[1])
rs._night_penalty=orig_night
