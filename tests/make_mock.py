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
def node(i,la,lo,name):return {"type":"node","id":i,"lat":la,"lon":lo,"tags":{"name":name}}
def way(i,la,lo,s,name,tags=None):
    g=[{"lat":la,"lon":lo},{"lat":la,"lon":lo+s},{"lat":la+s,"lon":lo+s},{"lat":la+s,"lon":lo},{"lat":la,"lon":lo}]
    return {"type":"way","id":i,"bounds":{"minlat":la,"minlon":lo,"maxlat":la+s,"maxlon":lo+s},"geometry":g,"tags":dict({"name":name},**(tags or {}))}
data={}
data['park']=[way(1000+i,la,lo,random.uniform(.001,.004),f'公園{i}',{"leisure":"park"}) for i,(la,lo) in enumerate(pts(500))]
data['park'].append({"type":"relation","id":9,"bounds":{"minlat":25.03,"minlon":121.53,"maxlat":25.034,"maxlon":121.537},
  "members":[{"type":"way","ref":1,"role":"outer","geometry":[{"lat":25.03,"lon":121.53},{"lat":25.03,"lon":121.537},{"lat":25.034,"lon":121.537},{"lat":25.034,"lon":121.53},{"lat":25.03,"lon":121.53}]}],"tags":{"name":"大安森林公園","leisure":"park"}})
data['green']=[way(2000+i,la,lo,random.uniform(.002,.01),'',{"landuse":"forest"}) for i,(la,lo) in enumerate(pts(400))]
data['school']=[way(3000+i,la,lo,.002,f'國小{i}',{"amenity":"school"}) if i%2 else node(3000+i,la,lo,f'幼兒園{i}') for i,(la,lo) in enumerate(pts(600))]
data['sport']=[node(4000+i,la,lo,f'球場{i}') for i,(la,lo) in enumerate(pts(500))]
data['bus']=[node(5000+i,la,lo,f'站牌{i}') for i,(la,lo) in enumerate(pts(4000))]
mrt=[node(6000+i,la,lo,f'捷運{i}站') for i,(la,lo) in enumerate(pts(110))]
mrt+= [node(7000+i,m['lat']+.0003,m['lon'],m['tags']['name'].replace('站','')) for i,m in enumerate(mrt[:20])]  # duplicates to test dedupe
data['mrt']=mrt
json.dump(data,open(os.path.join(HERE,'mock.json'),'w'),ensure_ascii=False)
print({k:len(v) for k,v in data.items()})
