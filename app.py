from flask import Flask, request, render_template_string
import requests
import math
import os
import re

app = Flask(__name__)

USE_AREA_DESCRIPTIONS = {
    "第一種低層住居専用地域": "低層の戸建住宅を中心とした、静かで落ち着いた住環境を守る地域です。住宅のほか、小規模な店舗兼用住宅や学校などは建てられますが、大きな店舗・事務所・ホテルなどは原則建てられません。",
    "第二種低層住居専用地域": "低層住宅の良好な住環境を守る地域です。第一種低層住居専用地域より少し用途が広く、一定規模までの日用品店や飲食店なども建てられます。",
    "田園住居地域": "農地と低層住宅が調和した環境を守る地域です。住宅のほか、農産物の直売所や農家レストランなど、農業と関連する一定の施設も建てられます。",
    "第一種中高層住居専用地域": "マンションなどの中高層住宅を中心に、良好な住環境を守る地域です。戸建住宅のほか、病院・大学・一定規模までの店舗なども建てられますが、大規模な店舗や娯楽施設などは制限されます。",
    "第二種中高層住居専用地域": "中高層住宅を中心とした住環境を守る地域です。第一種中高層住居専用地域より用途の幅が広く、一定規模の店舗や事務所なども建てられます。",
    "第一種住居地域": "住宅の住環境を守りながら、生活に必要な店舗や事務所なども建てられる地域です。住宅・マンションのほか、一定規模までの店舗、事務所、ホテルなども建築できます。",
    "第二種住居地域": "住宅を中心としながら、第一種住居地域より幅広い店舗・事務所・娯楽施設なども認められる地域です。幹線道路沿いなどで見られることがあります。",
    "準住居地域": "幹線道路沿いなどで、自動車関連施設と住宅が調和するよう定められた地域です。住宅のほか、店舗・事務所・自動車関連施設など幅広い建物が建てられます。",
    "近隣商業地域": "近隣の住民が日常の買い物をする店舗などの利便を図る地域です。住宅やマンションのほか、店舗・事務所など幅広い建物が建てられ、商店街や駅周辺などで多く見られます。",
    "商業地域": "店舗・事務所などの商業施設の利便を優先する地域です。百貨店、飲食店、オフィスなど幅広い用途が認められ、住宅やマンションも建築できます。駅前や中心市街地などで多く見られます。",
    "準工業地域": "住宅・店舗と工場などが共存する地域です。幅広い建物を建築できますが、周辺に工場や倉庫などがある場合があるため、住環境を確認することが大切です。",
    "工業地域": "工場の利便を図る地域です。工場のほか住宅や店舗も建てられますが、学校・病院など建築できない用途があります。住宅を検討する場合は周辺の工場や交通量などの確認が重要です。",
    "工業専用地域": "工場の操業を優先する地域です。工場や倉庫などが中心で、住宅・マンション・学校・病院などは建築できません。住宅用地としては利用できない地域です。",
}

def normalize_use_area_name(name):
    if not name:
        return ""
    text = str(name).strip()
    for old, new in {"第１種":"第一種","第1種":"第一種","第２種":"第二種","第2種":"第二種"}.items():
        text = text.replace(old, new)
    return text

def use_area_description(name):
    return USE_AREA_DESCRIPTIONS.get(
        normalize_use_area_name(name),
        f"{name}の建築用途には個別の制限があります。建築できる建物の種類や規模は、計画内容と自治体の最新情報で確認してください。"
    )

def latlon_to_tile(lat, lon, zoom=15):
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y

def point_on_segment(px, py, x1, y1, x2, y2, eps=1e-10):
    cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
    if abs(cross) > eps:
        return False
    return min(x1,x2)-eps <= px <= max(x1,x2)+eps and min(y1,y2)-eps <= py <= max(y1,y2)+eps

def point_in_ring(px, py, ring):
    inside = False
    j = len(ring)-1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if point_on_segment(px,py,xi,yi,xj,yj):
            return True
        if (yi > py) != (yj > py):
            x_cross = (xj-xi)*(py-yi)/(yj-yi)+xi
            if px < x_cross:
                inside = not inside
        j = i
    return inside

def point_in_polygon(lon, lat, polygon):
    if not polygon or not point_in_ring(lon, lat, polygon[0]):
        return False
    return not any(point_in_ring(lon, lat, hole) for hole in polygon[1:])

def point_in_geometry(lon, lat, geometry):
    typ = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if typ == "Polygon":
        return point_in_polygon(lon, lat, coords)
    if typ == "MultiPolygon":
        return any(point_in_polygon(lon, lat, p) for p in coords)
    return False

def get_api_features(api_code, x, y, zoom=15):
    api_key = os.environ.get("REINFOLIB_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("REINFOLIB_API_KEY が設定されていません。")
    r = requests.get(
        f"https://www.reinfolib.mlit.go.jp/ex-api/external/{api_code}",
        headers={"Ocp-Apim-Subscription-Key": api_key},
        params={"response_format":"geojson","z":zoom,"x":x,"y":y},
        timeout=15
    )
    r.raise_for_status()
    return r.json().get("features", [])

def get_school_district_features_multizoom(api_code, lat, lon):
    all_features, success_count, zooms_with_data, seen = [], 0, [], set()
    for zoom in range(15,10,-1):
        x,y = latlon_to_tile(lat,lon,zoom)
        radius = 1 if zoom in (15,14) else 0
        got = False
        for dx in range(-radius,radius+1):
            for dy in range(-radius,radius+1):
                try:
                    features = get_api_features(api_code,x+dx,y+dy,zoom)
                    success_count += 1
                    got = got or bool(features)
                    for f in features:
                        p=f.get("properties",{}); g=f.get("geometry",{})
                        key=(p.get("A27_003") or p.get("A32_003") or p.get("_id"), str(g))
                        if key not in seen:
                            seen.add(key); all_features.append(f)
                except Exception:
                    continue
        if got: zooms_with_data.append(zoom)
    return all_features, success_count, zooms_with_data

def find_matching_features(features, lon, lat):
    return [f for f in features if point_in_geometry(lon,lat,f.get("geometry",{}))]

def normalize_japanese_address_for_school(address):
    s = address or ""
    nums={"一":"1","二":"2","三":"3","四":"4","五":"5","六":"6","七":"7","八":"8","九":"9"}
    for k,v in nums.items():
        s=s.replace(f"{k}丁目",f"{v}丁目")
    s=s.replace(" ","").replace("　","")
    s=s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))

    # 岐阜市などで「○○1-25」と入力された場合も、
    # 学区表の「○○1丁目」と照合できるように表記を補正する。
    # 町名直後の最初の数字だけを丁目として扱い、番地部分は残す。
    chome_stems = [
        "茜部大野", "茜部大川", "茜部新所", "茜部神清寺",
        "茜部寺屋敷", "茜部中島", "茜部野瀬", "茜部菱野",
        "茜部本郷", "水主町", "境川", "茜部辰新",
    ]
    for stem in chome_stems:
        s=re.sub(rf"({re.escape(stem)})([1-9])[-－ー]", rf"\1\2丁目", s)
    return s

def get_school_district_fallback(address):
    s=normalize_japanese_address_for_school(address)

    # 岐阜市：公式の通学区域規則に基づく補完（段階整備）
    # 茜部小学校区は、岐阜市例規集「別表第1」で次の区域とされています。
    # 中学校は「別表第2」により、茜部小学校区の全域が加納中学校区です。
    if "岐阜市" in s:
        akanabe_areas = [
            "茜部大野1丁目", "茜部大野2丁目",
            "茜部大川1丁目", "茜部大川2丁目",
            "茜部新所1丁目", "茜部新所2丁目", "茜部新所3丁目", "茜部新所4丁目",
            "茜部神清寺1丁目", "茜部神清寺2丁目",
            "茜部寺屋敷1丁目", "茜部寺屋敷2丁目", "茜部寺屋敷3丁目",
            "茜部中島1丁目", "茜部中島2丁目", "茜部中島3丁目",
            "茜部野瀬1丁目", "茜部野瀬2丁目", "茜部野瀬3丁目",
            "茜部菱野1丁目", "茜部菱野2丁目", "茜部菱野3丁目", "茜部菱野4丁目",
            "茜部本郷1丁目", "茜部本郷2丁目", "茜部本郷3丁目",
            "水主町1丁目", "水主町2丁目",
            "境川1丁目", "境川2丁目", "境川3丁目", "境川4丁目", "境川5丁目",
            "茜部辰新1丁目", "茜部辰新2丁目",
        ]
        # 「茜部」「茜町」は丁目を伴わない町名として規則に記載。
        if any(area in s for area in akanabe_areas) or re.search(r"岐阜市茜部(?:[-－ー0-9]|$)", s) or "岐阜市茜町" in s:
            return {
                "elementary":"岐阜市立茜部小学校",
                "junior_high":"岐阜市立加納中学校",
                "source":"岐阜市公式通学区域規則"
            }

    # 既存の補完参考データ
    if "高山市" in s and ("岡本町1丁目" in s or re.search(r"岡本町1[-－ー]",s)):
        return {
            "elementary":"高山市立南小学校",
            "junior_high":"高山市立松倉中学校",
            "source":"補完参考"
        }
    return None

def flood_rank_text(rank):
    m={1:"0.5m未満",2:"0.5m以上～3.0m未満",3:"3.0m以上～5.0m未満",4:"5.0m以上～10.0m未満",5:"10.0m以上～20.0m未満",6:"20.0m以上"}
    try: return m.get(int(rank),f"ランク{rank}")
    except: return "不明"

def haversine_distance_m(lat1,lon1,lat2,lon2):
    r=6371000.0
    p1,p2=math.radians(lat1),math.radians(lat2)
    dp=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return r*2*math.atan2(math.sqrt(a),math.sqrt(1-a))

def walking_minutes_estimate(distance_m):
    return max(1,math.ceil(distance_m*1.25/80.0))

YAHOO_LOCAL_SEARCH_ENDPOINT="https://map.yahooapis.jp/search/local/V1/localSearch"

def _yahoo_features(data):
    features=(data or {}).get("Feature")
    if features is None and isinstance((data or {}).get("YDF"),dict):
        features=data["YDF"].get("Feature")
    if isinstance(features,dict): return [features]
    return features if isinstance(features,list) else []

def _yahoo_coordinates(feature):
    coords=(feature.get("Geometry") or {}).get("Coordinates")
    if isinstance(coords,str):
        p=coords.split(",")
        if len(p)>=2:
            try:return float(p[1]),float(p[0])
            except:return None,None
    return None,None

def get_yahoo_drugstores(lat,lon,radius_km=5,limit_count=3):
    client_id=os.environ.get("YAHOO_CLIENT_ID","").strip()
    if not client_id:return [],"Yahoo! Client IDが見つかりません。"
    queries=["ドラッグストア","ウエルシア","スギ薬局","クスリのアオキ","Vドラッグ","ゲンキー","ドラッグユタカ","マツモトキヨシ","ココカラファイン"]
    candidates=[]; seen=set(); any_success=False
    for q in queries:
        try:
            r=requests.get(YAHOO_LOCAL_SEARCH_ENDPOINT,params={"appid":client_id,"query":q,"lat":lat,"lon":lon,"dist":radius_km,"sort":"geo","results":50,"output":"json","detail":"standard"},timeout=25,headers={"User-Agent":"RealEstateEasySurvey-Web/1.0"})
            r.raise_for_status(); data=r.json(); any_success=True
        except Exception: continue
        for f in _yahoo_features(data):
            name=(f.get("Name") or "").strip()
            flat,flon=_yahoo_coordinates(f)
            if not name or flat is None: continue
            d=haversine_distance_m(lat,lon,flat,flon)
            key=(name,round(flat,6),round(flon,6))
            if d<=radius_km*1000 and key not in seen:
                seen.add(key); candidates.append({"name":name,"distance_m":round(d),"walk_min":walking_minutes_estimate(d),"lat":flat,"lon":flon})
    candidates.sort(key=lambda x:x["distance_m"])
    return candidates[:limit_count], None if any_success else "Yahoo!ローカルサーチを取得できませんでした。"

def get_nearby_shops(lat,lon,radius=3000,limit_count=3):
    key=os.environ.get("GEOAPIFY_API_KEY","").strip()
    nearby={"convenience":[],"supermarket":[],"drugstore":[],"station":[]}
    if not key:return nearby,"Geoapify APIキーが見つかりません。"
    endpoint="https://api.geoapify.com/v2/places"; any_success=False
    def fetch(cat):
        nonlocal any_success
        r=requests.get(endpoint,params={"categories":cat,"filter":f"circle:{lon},{lat},{radius}","bias":f"proximity:{lon},{lat}","limit":30,"lang":"ja","apiKey":key},timeout=25,headers={"User-Agent":"RealEstateEasySurvey-Web/1.0"})
        r.raise_for_status(); any_success=True
        out=[]
        for f in r.json().get("features",[]):
            p=f.get("properties",{}); g=f.get("geometry",{}); c=g.get("coordinates") or []
            flon=p.get("lon"); flat=p.get("lat")
            if flon is None and len(c)>=2: flon,flat=c[0],c[1]
            if flon is None: continue
            name=p.get("name") or p.get("address_line1") or p.get("formatted") or "名称不明"
            d=p.get("distance")
            try:d=float(d)
            except:d=haversine_distance_m(lat,lon,flat,flon)
            out.append({"name":name,"distance_m":round(d),"walk_min":walking_minutes_estimate(d),"lat":flat,"lon":flon})
        return out
    def top(items):
        seen=set(); out=[]
        for x in sorted(items,key=lambda v:v["distance_m"]):
            k=(x["name"],round(x["lat"],5),round(x["lon"],5))
            if k not in seen:
                seen.add(k); out.append(x)
            if len(out)>=limit_count: break
        return out
    for k,cat in [("convenience","commercial.convenience"),("supermarket","commercial.supermarket")]:
        try: nearby[k]=top(fetch(cat))
        except: pass
    st=[]
    for cat in ["public_transport.train","public_transport.light_rail"]:
        try: st.extend(fetch(cat))
        except: pass
    nearby["station"]=top(st)
    return nearby, None if any_success else "周辺施設を取得できませんでした。"

def get_walking_routes(lat,lon,nearby):
    refs=[]; targets=[]
    for key in ("convenience","supermarket","drugstore","station"):
        for i,f in enumerate(nearby.get(key,[])):
            refs.append((key,i)); targets.append({"lat":f["lat"],"lon":f["lon"]})
    if not targets:return {}
    payload={"sources":[{"lat":lat,"lon":lon}],"targets":targets,"costing":"pedestrian","units":"kilometers","verbose":False}
    data=None
    for ep in ["https://valhalla1.openstreetmap.de/sources_to_targets","https://valhalla.openstreetmap.de/sources_to_targets"]:
        try:
            r=requests.post(ep,json=payload,timeout=30,headers={"User-Agent":"RealEstateEasySurvey-Web/1.0","X-Client-Id":"realestate-easy-survey-web"})
            r.raise_for_status(); data=r.json(); break
        except: pass
    if not data:return {}
    m=data.get("sources_to_targets",{})
    try: dr=m["durations"][0]; ds=m["distances"][0]
    except:return {}
    out={}
    for pos,ref in enumerate(refs):
        try:
            if dr[pos] is not None and ds[pos] is not None:
                out[ref]={"walk_min":max(1,math.ceil(float(dr[pos])/60)),"route_distance_m":round(float(ds[pos])*1000)}
        except: pass
    return out

def area_explanation(area_names):
    t=" / ".join(area_names)
    if "市街化調整区域" in t:
        return "市街化を抑制する区域で、原則として建物の建築や開発に制限があります。建築できるかどうかは、許可要件や土地の状況などを個別に確認する必要があります。"
    if "市街化区域" in t:
        return "すでに市街地となっている区域、または今後おおむね10年以内に優先的・計画的に市街化を進める区域です。用途地域などに応じて建てられる建物や規模が定められます。"
    if "非線引き" in t or "区域区分非設定" in t or ("都市計画区域" in t and "市街化区域" not in t and "市街化調整区域" not in t):
        return "市街化区域と市街化調整区域の区分を定めていない都市計画区域です。市街化調整区域とは異なり、一律に建築を抑制する区域ではありません。用途地域や道路・建築基準法など、個別の条件を確認する必要があります。"
    return "区域区分によって建築や開発の条件が異なります。詳細は自治体で確認してください。"

def perform_search(address):
    geo=requests.get("https://msearch.gsi.go.jp/address-search/AddressSearch",params={"q":address},timeout=10)
    geo.raise_for_status(); gd=geo.json()
    if not gd: raise ValueError("住所が見つかりませんでした。住所を少し短くしてお試しください。")
    lon,lat=gd[0]["geometry"]["coordinates"]
    x,y=latlon_to_tile(lat,lon,15)

    codes={}
    for code in ["XKT001","XKT002","XKT014","XKT003","XKT026","XKT029"]:
        features=get_api_features(code,x,y)
        codes[code]=find_matching_features(features,lon,lat)

    ef,es,_=get_school_district_features_multizoom("XKT004",lat,lon)
    jf,js,_=get_school_district_features_multizoom("XKT005",lat,lon)
    em=find_matching_features(ef,lon,lat); jm=find_matching_features(jf,lon,lat)

    nearby,shop_error=get_nearby_shops(lat,lon)
    drugs,drug_error=get_yahoo_drugstores(lat,lon)
    if drugs: nearby["drugstore"]=drugs
    routes=get_walking_routes(lat,lon,nearby) if not shop_error else {}

    area_names=[]
    for f in codes["XKT001"]:
        n=f.get("properties",{}).get("area_classification_ja","")
        if n and n not in area_names: area_names.append(n)

    uses=[]
    seen=set()
    for f in codes["XKT002"]:
        p=f.get("properties",{})
        k=(p.get("use_area_ja"),p.get("u_building_coverage_ratio_ja"),p.get("u_floor_area_ratio_ja"))
        if k not in seen:
            seen.add(k)
            uses.append({"name":p.get("use_area_ja","不明"),"coverage":p.get("u_building_coverage_ratio_ja","不明"),"floor":p.get("u_floor_area_ratio_ja","不明"),"description":use_area_description(p.get("use_area_ja","不明"))})

    fire=[]
    for f in codes["XKT014"]:
        n=f.get("properties",{}).get("fire_prevention_ja","")
        if n and n not in fire: fire.append(n)

    plan_names=[]
    for f in codes["XKT003"]:
        p=f.get("properties",{})
        for k in ("kubun_name_ja","area_classification_ja"):
            n=str(p.get(k) or "").strip()
            if n and n not in plan_names: plan_names.append(n)
    in_res=any("居住誘導区域" in n for n in plan_names)
    in_plan=any("立地適正化計画区域" in n for n in plan_names)
    residence="区域内（公開GIS判定）" if in_res else ("区域外（公開GIS判定）" if in_plan else "公開GISでは判定できません（要自治体確認）")

    floods=[]
    for f in codes["XKT026"]:
        p=f.get("properties",{})
        item=(p.get("A31a_202","河川名不明"),flood_rank_text(p.get("A31a_205","")))
        if item not in floods: floods.append(item)

    sediments=[]
    pm={1:"土石流",2:"急傾斜地の崩壊",3:"地すべり"}
    am={1:"土砂災害警戒区域",2:"土砂災害特別警戒区域"}
    for f in codes["XKT029"]:
        p=f.get("properties",{})
        try: phenomenon=pm.get(int(p.get("A33_001")),str(p.get("A33_001","")))
        except: phenomenon=str(p.get("A33_001",""))
        try: typ=am.get(int(p.get("A33_002")),str(p.get("A33_002","")))
        except: typ=str(p.get("A33_002",""))
        item={"phenomenon":phenomenon,"type":typ,"name":p.get("A33_005","")}
        if item not in sediments: sediments.append(item)

    def names(matches, code):
        out=[]
        for f in matches:
            p=f.get("properties",{})
            school=p.get("A27_004_ja" if code=="e" else "A32_004_ja","")
            auth=p.get("A27_002" if code=="e" else "A32_002","")
            if school:
                d=f"{auth} {school}".strip() if auth and auth not in school else school
                if d not in out: out.append(d)
        return out
    en=names(em,"e"); jn=names(jm,"j")
    fb=get_school_district_fallback(address)
    if fb:
        source_label = fb.get("source", "補完参考")
        elementary_fallback = f'{fb["elementary"]}（{source_label}）'
        junior_fallback = f'{fb["junior_high"]}（{source_label}）'
    else:
        elementary_fallback = None
        junior_fallback = None
    elementary=" / ".join(en) if en else (elementary_fallback if fb else ("公開データで判定できません（要自治体確認）" if es>0 else "学区データを取得できません"))
    junior=" / ".join(jn) if jn else (junior_fallback if fb else ("公開データで判定できません（要自治体確認）" if js>0 else "学区データを取得できません"))

    def facility_list(key):
        out=[]
        for i,f in enumerate(nearby.get(key,[])[:3]):
            r=routes.get((key,i))
            item=dict(f)
            if r:
                item.update({"distance_text":f"徒歩距離 約{r['route_distance_m']}m","time_text":f"徒歩 約{r['walk_min']}分","route":True})
            else:
                item.update({"distance_text":f"直線距離 約{f['distance_m']}m","time_text":f"徒歩目安 約{f['walk_min']}分","route":False})
            out.append(item)
        return out

    return {
        "address":address,
        "area_names":area_names or ["該当データなし"],
        "area_explanation":area_explanation(area_names),
        "uses":uses,
        "fire":" / ".join(fire) if fire else "防火・準防火の公開GIS該当なし（要自治体確認）",
        "residence":residence,
        "floods":floods,
        "sediments":sediments,
        "facilities":{
            "コンビニ":facility_list("convenience"),
            "スーパー":facility_list("supermarket"),
            "ドラッグストア":facility_list("drugstore"),
            "最寄駅":facility_list("station"),
        },
        "elementary":elementary,
        "junior":junior,
    }

HTML = r'''<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0f4c81">
<title>不動産かんたん調査｜東海三県 営業版</title>
<style>
:root{--bg:#f3f6f9;--card:#fff;--ink:#17212b;--muted:#657381;--accent:#0f4c81;--warn:#9a5b00;--danger:#a52820;--soft:#eef5fa}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,sans-serif;background:var(--bg);color:var(--ink)}
header{background:linear-gradient(135deg,#0f4c81,#176ea8);color:#fff;padding:18px 16px 22px;position:sticky;top:0;z-index:5;box-shadow:0 2px 10px #0002}
.wrap{max-width:920px;margin:auto;padding:14px}h1{font-size:22px;margin:0 0 8px}.subtitle{font-size:13px;opacity:.92;margin-bottom:12px}.scope{display:inline-block;font-size:11px;background:#ffffff24;padding:4px 8px;border-radius:999px;margin-bottom:12px}
form{display:flex;gap:8px}.address{flex:1;padding:13px 14px;border:0;border-radius:10px;font-size:16px;min-width:0}.btn{padding:0 18px;border:0;border-radius:10px;background:#fff;color:var(--accent);font-weight:700;font-size:15px;white-space:nowrap}
.card{background:#fff;border-radius:14px;padding:17px;margin:12px 0;box-shadow:0 2px 12px #15283b12;border:1px solid #e9eef2}.card h2{font-size:18px;margin:0 0 12px;color:var(--accent)}.card h3{font-size:15px;margin:16px 0 7px}
.row{display:grid;grid-template-columns:128px 1fr;gap:8px;padding:7px 0;border-bottom:1px solid #edf1f4}.row:last-child{border-bottom:0}.label{color:var(--muted);font-size:14px}.value{font-weight:600}.value.warn{color:var(--danger)}
.desc{background:#f6f9fb;border-left:4px solid #8bb7d4;padding:10px 12px;border-radius:8px;line-height:1.7;font-size:14px}.facility{padding:10px 0;border-bottom:1px solid #edf1f4}.facility:last-child{border-bottom:0}.facility b{display:block;margin-bottom:4px}.meta{font-size:13px;color:var(--muted)}
.notice{font-size:12px;line-height:1.65;color:var(--muted)}.error{background:#fff1f0;border:1px solid #ffd1cc;color:#8c2b20;padding:14px;border-radius:12px;margin:12px 0}.spinner{display:none;margin-left:8px}.loading .spinner{display:inline}.loading .btn{opacity:.7}footer{padding:12px 4px 32px;font-size:11px;color:#73808c;line-height:1.7}
@media(max-width:600px){header{padding-top:calc(14px + env(safe-area-inset-top))}.wrap{padding:10px}h1{font-size:20px}form{display:block}.address{width:100%;margin-bottom:8px}.btn{width:100%;height:46px}.card{border-radius:12px;padding:15px;margin:10px 0}.row{grid-template-columns:1fr;gap:2px}.label{font-size:12px}.value{font-size:15px}}
</style>
</head>
<body>
<header><div class="wrap" style="padding:0">
<h1>不動産かんたん調査</h1>
<div class="scope">愛知・岐阜・三重｜営業現場向け</div>
<div class="subtitle">土地・建築制限／ハザード／学区／生活情報をまとめて確認</div>
<form method="get" action="/" id="searchForm">
<input class="address" name="address" value="{{ address|e }}" placeholder="例：岐阜市○○町1-2-3" autocomplete="street-address">
<button class="btn" type="submit">この住所を調査 <span class="spinner">…</span></button>
</form></div></header>
<main class="wrap">
{% if error %}<div class="error">{{ error }}</div>{% endif %}
{% if r %}
<div class="card"><h2>📍 物件調査結果</h2><div class="row"><div class="label">所在地</div><div class="value">{{ r.address }}</div></div></div>
<div class="card"><h2>🏠 土地・建築情報</h2>
<div class="row"><div class="label">区域区分</div><div class="value">{{ r.area_names|join(' / ') }}</div></div>
<h3>区域区分について</h3><div class="desc">{{ r.area_explanation }}</div>
{% if r.uses %}{% for u in r.uses %}
<div class="row"><div class="label">用途地域</div><div class="value">{{ u.name }}</div></div>
<div class="row"><div class="label">建ぺい率</div><div class="value">{{ u.coverage }}</div></div>
<div class="row"><div class="label">容積率</div><div class="value">{{ u.floor }}</div></div>
<h3>用途地域について</h3><div class="desc">{{ u.description }}</div>
{% endfor %}{% else %}<div class="row"><div class="label">用途地域</div><div class="value">該当データなし</div></div>{% endif %}
<div class="row"><div class="label">防火・準防火</div><div class="value {% if '準防火' in r.fire or '防火地域' in r.fire %}warn{% endif %}">{{ r.fire }}</div></div>
<div class="notice">※22条区域は営業画面の主要項目から外しました。必要時は自治体の最新情報で確認してください。</div>
</div>
<div class="card"><h2>🌊 ハザード情報</h2>
{% if r.floods %}<div class="row"><div class="label">洪水浸水想定</div><div class="value warn">⚠ 区域内</div></div>{% for river,depth in r.floods %}<div class="row"><div class="label">河川・浸水深</div><div class="value">{{ river }} ／ {{ depth }}</div></div>{% endfor %}
{% else %}<div class="row"><div class="label">洪水浸水想定</div><div class="value">公開データ上の該当なし</div></div>{% endif %}
{% if r.sediments %}<div class="row"><div class="label">土砂災害</div><div class="value warn">⚠ 区域内</div></div>{% for s in r.sediments %}<div class="row"><div class="label">{{ s.phenomenon }}</div><div class="value">{{ s.type }}{% if s.name %} ／ {{ s.name }}{% endif %}</div></div>{% endfor %}
{% else %}<div class="row"><div class="label">土砂災害</div><div class="value">公開データ上の該当なし</div></div>{% endif %}
<h3>追加確認項目</h3>
<div class="row"><div class="label">内水</div><div class="value">自動判定準備中（自治体確認）</div></div>
<div class="row"><div class="label">高潮</div><div class="value">自動判定準備中（自治体確認）</div></div>
<div class="row"><div class="label">津波</div><div class="value">自動判定準備中（自治体確認）</div></div>
<div class="notice">※「公開データ上の該当なし」は安全を保証するものではありません。</div>
</div>
<div class="card"><h2>🏫 学区情報</h2>
<div class="row"><div class="label">小学校区</div><div class="value">{{ r.elementary }}</div></div>
<div class="row"><div class="label">中学校区</div><div class="value">{{ r.junior }}</div></div>
<div class="notice">※学区は参考情報です。岐阜市は公式通学区域規則による補完を段階整備中です。判定できない場合に学校名を推測せず「要自治体確認」と表示します。最新の指定校・通学区域は各自治体で確認してください。</div>
</div>
<div class="card"><h2>🛒 生活情報</h2>
{% for label,items in r.facilities.items() %}<h3>{{ label }}</h3>
{% if items %}{% for f in items %}<div class="facility"><b>{{ loop.index }}. {{ f.name }}</b><div class="meta">{{ f.distance_text }}　{{ f.time_text }}{% if not f.route %}（概算）{% endif %}</div></div>{% endfor %}
{% else %}<div class="meta">登録データなし</div>{% endif %}{% endfor %}
</div>
<div class="card"><h2>📋 その他の注意情報</h2>
<div class="row"><div class="label">居住誘導区域</div><div class="value">{{ r.residence }}</div></div>
<div class="row"><div class="label">高度地区</div><div class="value">自動判定準備中（自治体確認）</div></div>
<div class="row"><div class="label">都市計画道路</div><div class="value">自動判定準備中（自治体確認）</div></div>
<div class="row"><div class="label">盛土規制</div><div class="value">自動判定準備中（自治体確認）</div></div>
<div class="row"><div class="label">地区計画・景観等</div><div class="value">必要に応じ自治体確認</div></div>
<div class="notice">※「準備中」の項目は現時点で自動判定していません。誤って「該当なし」と表示しないための安全表示です。</div>
</div>
<div class="card"><h2>情報源・注意事項</h2><div class="notice">
・用途地域等：不動産情報ライブラリ（国土交通省）<br>・防火・準防火：不動産情報ライブラリ XKT014<br>・居住誘導区域：不動産情報ライブラリ XKT003<br>・洪水：不動産情報ライブラリ XKT026<br>・土砂災害：不動産情報ライブラリ XKT029<br>・学区：不動産情報ライブラリの公開データを基本とし、岐阜市は公式通学区域規則による補完を段階整備中<br>・公開GISで未判定の場合は「指定なし」「区域外」と断定しません。<br>・契約・重要事項説明に使用する場合は、必ず最新の行政情報を確認してください。
</div></div>{% endif %}
<footer>東海三県（愛知・岐阜・三重）の営業利用を優先して整備中です。<br>コンビニ・スーパー・駅：Geoapify Places API ／ ドラッグストア：Yahoo!ローカルサーチAPI<br>徒歩経路：OpenStreetMap道路データを利用する公開ルートサービス（取得不可時は概算）<br>© OpenStreetMap contributors　／　Web Services by Yahoo! JAPAN</footer>
</main>
<script>document.getElementById('searchForm').addEventListener('submit',function(){this.classList.add('loading');this.querySelector('.btn').disabled=true;});</script>
</body></html>'''

@app.route("/")
def index():
    address=(request.args.get("address") or "").strip()
    result=None; error=None
    if address:
        try: result=perform_search(address)
        except Exception as e: error=str(e)
    return render_template_string(HTML,address=address,r=result,error=error)

if __name__=="__main__":
    print("不動産かんたん調査 Web版")
    print("PC: http://127.0.0.1:5000")
    print("スマホ: 同じWi-Fi内で http://このPCのIPアドレス:5000")
    app.run(host="0.0.0.0",port=5000,debug=False)
