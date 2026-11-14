import json, random, os
HERE=os.path.dirname(os.path.abspath(__file__))
random.seed(1)
G=json.load(open(os.path.join(HERE,'..','taipei_districts.geojson')))
D=[{'type':f['geometry']['type'],'coords':f['geometry']['coordinates']} for f in G['features']]
def ring_contains(r,x,y):
    c=False;j=len(r)-1
    for i in range(len(r)):
        xi,yi=r[i];xj,yj=r[j]
        if (yi>y)!=(yj>y) and x<(xj-xi)*(y-yi)/(yj-yi)+xi:c=not c
        j=i
    return c
def in_city(la,lo):
    for d in D:
        ps=d['coords'] if d['type']=='MultiPolygon' else [d['coords']]
        if any(ring_contains(p[0],lo,la) for p in ps):return True
    return False
def pts(n):
    out=[]
    while len(out)<n:
        la=24.96+random.random()*0.25;lo=121.45+random.random()*0.22
        # denser in the urban core
        if in_city(la,lo) and (random.random()<0.25 or (25.01<la<25.09 and 121.50<lo<121.62)):out.append((la,lo))
    return out
def node(i,la,lo,name,**tags):return {"type":"node","id":i,"lat":la,"lon":lo,"tags":dict({"name":name},**tags)}
def way(i,la,lo,s,name,tags=None):
    g=[{"lat":la,"lon":lo},{"lat":la,"lon":lo+s},{"lat":la+s,"lon":lo+s},{"lat":la+s,"lon":lo},{"lat":la,"lon":lo}]
    return {"type":"way","id":i,"bounds":{"minlat":la,"minlon":lo,"maxlat":la+s,"maxlon":lo+s},"geometry":g,"tags":dict({"name":name},**(tags or {}))}
data={}
data['park']=[way(1000+i,la,lo,random.uniform(.001,.004),f'公園{i}',{"leisure":"park"}) for i,(la,lo) in enumerate(pts(500))]
data['park'].append({"type":"relation","id":9,"bounds":{"minlat":25.03,"minlon":121.53,"maxlat":25.034,"maxlon":121.537},
  "members":[{"type":"way","ref":1,"role":"outer","geometry":[{"lat":25.03,"lon":121.53},{"lat":25.03,"lon":121.537},{"lat":25.034,"lon":121.537},{"lat":25.034,"lon":121.53},{"lat":25.03,"lon":121.53}]}],"tags":{"name":"大安森林公園","leisure":"park"}})
data['school']=[way(3000+i,la,lo,.002,f'國小{i}',{"amenity":"school"}) if i%2 else node(3000+i,la,lo,f'幼兒園{i}',amenity='kindergarten') for i,(la,lo) in enumerate(pts(600))]
data['sport']=[node(4000+i,la,lo,f'球場{i}',leisure='pitch') for i,(la,lo) in enumerate(pts(500))]
data['market']=[way(4500+i,la,lo,.0006,f'全聯{i}',{"shop":"supermarket"}) if i%3==0 else node(4500+i,la,lo,f'超市{i}',shop='supermarket') for i,(la,lo) in enumerate(pts(300))]
data['bus']=[node(5000+i,la,lo,f'站牌{i}',highway='bus_stop') for i,(la,lo) in enumerate(pts(4000))]
mrt=[node(6000+i,la,lo,f'捷運{i}站',railway='station',station='subway') for i,(la,lo) in enumerate(pts(110))]
mrt+= [node(7000+i,m['lat']+.0003,m['lon'],m['tags']['name'].replace('站',''),public_transport='station',subway='yes') for i,m in enumerate(mrt[:20])]  # duplicates to test dedupe
data['mrt']=mrt
pass
print({k:len(v) for k,v in data.items()})

# ---- 路線（模擬 OSM PTv2 route relation，out geom 格式）----
import math
def dist(a,b):return math.hypot((a[0]-b[0])*110540,(a[1]-b[1])*101000)
bus=[(n['lat'],n['lon']) for n in data['bus']]
routes=[]
for r in range(60):
    hubs=[i for i in range(len(bus)) if 25.025<bus[i][0]<25.04 and 121.535<bus[i][1]<121.55][:6]
    cur=hubs[r%6] if r<36 else random.randrange(len(bus));seq=[cur];heading=random.uniform(0,2*math.pi)
    for _ in range(random.randint(8,15)):
        p=bus[seq[-1]]
        cand=[i for i in range(len(bus)) if i not in seq and 150<dist(bus[i],p)<900]
        if not cand:break
        cand.sort(key=lambda i:abs(math.atan2(bus[i][0]-p[0],bus[i][1]-p[1])-heading))
        seq.append(cand[0])
    if len(seq)<4:continue
    members=[]
    for k,i in enumerate(seq):
        members.append({"type":"node","ref":data['bus'][i]['id'],"role":"stop","lat":bus[i][0],"lon":bus[i][1]})
    for k in range(len(seq)-1):
        A,B=bus[seq[k]],bus[seq[k+1]]
        # 模擬沿道路行駛：先沿東西向道路、再轉南北向（L 形），中間還有一個彎
        g=[{"lat":A[0],"lon":A[1]},{"lat":A[0],"lon":(A[1]+B[1])/2},{"lat":A[0]+(B[0]-A[0])*0.3,"lon":(A[1]+B[1])/2+0.0004},{"lat":A[0]+(B[0]-A[0])*0.3,"lon":B[1]},{"lat":B[0],"lon":B[1]}]
        if k%3==1:g=g[::-1]   # 測試反向 way 的串接
        members.append({"type":"way","ref":900000+r*100+k,"role":"","geometry":g})
    iv=[ '6','12','00:20',None ][(r//6)%4]
    tags={"type":"route","route":"bus","ref":str(200+r),"from":f"起站{r}","to":f"終站{r}","name":f"公車{200+r}"}
    if iv:tags["interval"]=iv
    routes.append({"type":"relation","id":800000+r,"members":members,"tags":tags})
mrt=[(n['lat'],n['lon']) for n in data['mrt'][:110]]
for r,col in enumerate(['#e3002c','#0070bd','#008659']):
    seq=sorted(random.sample(range(len(mrt)),10),key=lambda i:mrt[i][1]+r*mrt[i][0])
    members=[{"type":"node","ref":data['mrt'][i]['id'],"role":"stop","lat":mrt[i][0],"lon":mrt[i][1]} for i in seq]
    members+=[{"type":"way","ref":950000+r*100+k,"role":"","geometry":[{"lat":mrt[seq[k]][0],"lon":mrt[seq[k]][1]},{"lat":mrt[seq[k+1]][0],"lon":mrt[seq[k+1]][1]}]} for k in range(len(seq)-1)]
    routes.append({"type":"relation","id":850000+r,"members":members,"tags":{"type":"route","route":"subway","ref":"BR"[r%2]+str(r),"to":f"捷運終點{r}","colour":col}})
json.dump({'layers':data,'routes':routes},open(os.path.join(HERE,'mock.json'),'w'),ensure_ascii=False)
print('routes',len(routes))
