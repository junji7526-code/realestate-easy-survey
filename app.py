# BUILD: v2.5-gifu-official-priority-yabuta-fixed (2026-08-31)
from flask import Flask, request, render_template_string
import requests
import math
import os
import re
from io import BytesIO
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

app = Flask(__name__)
BUILD_VERSION = "ATRIS v1.0-preview3-20260910"

USE_AREA_DESCRIPTIONS = {
    "第一種低層住居専用地域": "低い住宅を中心とした、静かな住環境を守る地域です。大きなお店やホテルなどは、原則として建てられません。",
    "第二種低層住居専用地域": "低い住宅を中心とした地域です。住宅のほか、小さなお店や飲食店なども建てられます。",
    "田園住居地域": "農地と低い住宅の環境を守る地域です。住宅のほか、農産物の直売所なども建てられます。",
    "第一種中高層住居専用地域": "マンションや住宅を中心とした地域です。病院や学校、小さなお店などは建てられますが、大きなお店や遊技施設などには制限があります。",
    "第二種中高層住居専用地域": "マンションや住宅を中心とした地域です。住宅のほか、お店や事務所なども建てられます。",
    "第一種住居地域": "住宅の環境を守りながら、お店や事務所なども建てられる地域です。",
    "第二種住居地域": "住宅を中心としながら、お店や事務所なども建てられる地域です。第一種住居地域より建てられる建物の種類が多くなります。",
    "準住居地域": "大きな道路の近くなどで、住宅と自動車関係の施設がいっしょに建つ地域です。",
    "近隣商業地域": "近くに住む人が買い物をしやすいようにした地域です。住宅のほか、お店や事務所なども建てられます。",
    "商業地域": "お店や事務所を建てやすい地域です。住宅やマンションも建てられます。駅の近くなどに多い地域です。",
    "準工業地域": "住宅、お店、工場などが建つ地域です。近くに工場や倉庫があることもあるため、周りの様子も確認すると安心です。",
    "工業地域": "工場を建てやすい地域です。住宅やお店も建てられますが、学校や病院などは建てられません。",
    "工業専用地域": "工場や倉庫のための地域です。住宅やマンションは建てられません。",
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

def latlon_to_tile_pixel(lat, lon, zoom=15):
    """緯度経度を地理院タイル番号とタイル内ピクセルへ変換する。"""
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    xf = (lon + 180.0) / 360.0 * n
    yf = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return int(xf), int(yf), min(255, int((xf-int(xf))*256)), min(255, int((yf-int(yf))*256))

def get_inland_flood_status(lat, lon):
    """重ねるハザードマップの内水タイルを一次確認する。

    着色なしは、未整備・未収録との区別ができないため区域外と断定しない。
    """
    z = 16
    x, y, px, py = latlon_to_tile_pixel(lat, lon, z)
    url = f"https://disaportaldata.gsi.go.jp/raster/02_naisui_data/{z}/{x}/{y}.png"
    try:
        response = requests.get(url, timeout=12, headers={"User-Agent":"ATRIS/1.0"})
        if response.status_code == 404:
            return {"status":"公開データでは確認できません（自治体確認）","matched":False}
        response.raise_for_status()
        with Image.open(BytesIO(response.content)).convert("RGBA") as image:
            r, g, b, a = image.getpixel((px, py))
        # 透過又はほぼ無色の地点は、未掲載との区別ができないので断定しない。
        if a < 32 or (r > 245 and g > 245 and b > 245):
            return {"status":"公開データの着色を確認できません（自治体確認）","matched":False}
        return {"status":"内水浸水想定の着色あり（深さは自治体確認）","matched":True}
    except Exception:
        return {"status":"内水データを取得できません（自治体確認）","matched":False}

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

def get_api_features_neighborhood(api_code, x, y, zoom=15):
    """地区計画の縮尺差による取りこぼしを減らすため2縮尺を取得する。"""
    features=[]; seen=set()
    for current_zoom,current_x,current_y in ((zoom,x,y),(zoom-1,x//2,y//2)):
        try: items=get_api_features(api_code,current_x,current_y,current_zoom)
        except Exception: continue
        for feature in items:
            key=(str(feature.get("properties",{})),str(feature.get("geometry",{})))
            if key not in seen:
                seen.add(key); features.append(feature)
    return features

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

def _to_ascii_school_text(text):
    s = (text or "").replace(" ", "").replace("　", "")
    s = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    kanji_num = {"一":"1","二":"2","三":"3","四":"4","五":"5","六":"6","七":"7","八":"8","九":"9","十":"10"}
    for k, v in kanji_num.items():
        s = s.replace(k + "丁目", v + "丁目")
    return s

class _SchoolTableParser(__import__('html.parser').parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows=[]; self._in_tr=False; self._in_cell=False; self._cells=[]; self._buf=[]
    def handle_starttag(self, tag, attrs):
        tag=tag.lower()
        if tag=='tr': self._in_tr=True; self._cells=[]
        elif self._in_tr and tag in ('td','th'): self._in_cell=True; self._buf=[]
    def handle_endtag(self, tag):
        tag=tag.lower()
        if self._in_tr and self._in_cell and tag in ('td','th'):
            txt=''.join(self._buf).strip()
            self._cells.append(re.sub(r'\\s+',' ',txt))
            self._in_cell=False
        elif tag=='tr' and self._in_tr:
            if len(self._cells)>=2: self.rows.append(self._cells[:])
            self._in_tr=False
    def handle_data(self, data):
        if self._in_cell: self._buf.append(data)

_GIFU_SCHOOL_TABLE_CACHE = {"rows":None, "at":0}

def _fetch_gifu_school_rows():
    """岐阜市の学区表を取得。公式例規を優先し、取得不能時のみ公開学区表を補助利用。"""
    import time
    now=time.time()
    if _GIFU_SCHOOL_TABLE_CACHE["rows"] is not None and now-_GIFU_SCHOOL_TABLE_CACHE["at"] < 21600:
        return _GIFU_SCHOOL_TABLE_CACHE["rows"]
    urls=[
        "https://www1.g-reiki.net/gifu/reiki_honbun/i700RG00000608.html",
        "https://www.seishin-home.com/school/",
    ]
    for url in urls:
        try:
            r=requests.get(url,timeout=12,headers={"User-Agent":"Mozilla/5.0 RealEstateEasySurvey/2.2"})
            if r.status_code!=200 or len(r.text)<1000: continue
            parser=_SchoolTableParser(); parser.feed(r.text)
            rows=[]
            for cells in parser.rows:
                school=(cells[0] or '').strip(); area=''.join(cells[1:]).strip()
                if school.endswith('小学校') and area and '通学区域' not in area:
                    rows.append((school,area,url))
            if len(rows)>=25:
                _GIFU_SCHOOL_TABLE_CACHE.update(rows=rows,at=now)
                return rows
        except Exception:
            continue
    _GIFU_SCHOOL_TABLE_CACHE.update(rows=[],at=now)
    return []

def _split_jp_top_level(text):
    parts=[]; buf=[]; depth=0
    for ch in text or '':
        if ch in '(（': depth+=1
        elif ch in ')）' and depth>0: depth-=1
        if ch=='、' and depth==0:
            parts.append(''.join(buf)); buf=[]
        else: buf.append(ch)
    if buf: parts.append(''.join(buf))
    return [p.strip() for p in parts if p.strip()]

def _expand_area_candidates(area_text):
    """規則の「○○一、二、三丁目」型を、住所照合用の町丁目候補に展開する。"""
    raw=_to_ascii_school_text(area_text)
    parts=_split_jp_top_level(raw)
    out=[]; stem=None
    for i,part in enumerate(parts):
        p=part.strip().replace('まで','')
        # 単独の「2」「3丁目」などは直前の町名を引き継ぐ
        m=re.fullmatch(r'([1-9]|10)(丁目)?',p)
        if m and stem:
            out.append((f"{stem}{m.group(1)}丁目", False))
            continue
        # 「今町1」のように後続が「2、3、4丁目」なら丁目列の先頭とみなす
        m2=re.match(r'^(.+?)([1-9]|10)$',p)
        nxt=parts[i+1] if i+1<len(parts) else ''
        if m2 and re.fullmatch(r'([1-9]|10)(丁目)?',nxt):
            stem=m2.group(1)
            out.append((f"{stem}{m2.group(2)}丁目", False))
            continue
        # 明示的な丁目。番地条件が後ろにあれば complex とする
        m3=re.match(r'^(.+?)([1-9]|10)丁目(.*)$',p)
        if m3:
            stem=m3.group(1)
            tail=m3.group(3)
            out.append((f"{stem}{m3.group(2)}丁目", bool(tail)))
            continue
        # 番地条件付きの町名
        m4=re.match(r'^(.+?)(?:\\(|（)?[0-9,]+番',p)
        if m4:
            out.append((m4.group(1), True)); stem=None; continue
        # 字表記などは町名部分ごと候補にする
        base=re.split(r'[（(]',p)[0]
        base=re.sub(r'[0-9,]+番地.*$','',base)
        if base:
            out.append((base, bool(re.search(r'[0-9]番|除く|以北|以南|から',p))))
            stem=None
    # 長い候補を優先
    uniq=[]; seen=set()
    for name,complex_flag in sorted(out,key=lambda x:len(x[0]),reverse=True):
        if name and name not in seen:
            seen.add(name); uniq.append((name,complex_flag))
    return uniq

_GIFU_JHS_BY_ES = {
    '早田小学校':'岐阜清流中学校','則武小学校':'岐阜清流中学校',
    '岐阜小学校':'岐阜中央中学校','明郷小学校':'岐阜中央中学校',
    '徹明さくら小学校':'本荘中学校','本荘小学校':'本荘中学校',
    '白山小学校':'梅林中学校','梅林小学校':'梅林中学校','華陽小学校':'梅林中学校',
    '加納小学校':'加納中学校','茜部小学校':'加納中学校',
    '日野小学校':'長森中学校','長森北小学校':'長森中学校','長森西小学校':'長森中学校','長森東小学校':'長森中学校',
    '長良西小学校':'長良中学校',
    '島小学校':'島中学校','木田小学校':'島中学校','城西小学校':'島中学校',
    '岩野田小学校':'岩野田中学校','岩野田北小学校':'岩野田中学校',
    '市橋小学校':'精華中学校','鏡島小学校':'精華中学校',
    '岩小学校':'藍川中学校','芥見小学校':'藍川中学校',
    '三輪南小学校':'三輪中学校','三輪北小学校':'三輪中学校',
    '方県小学校':'岐北中学校','黒野小学校':'岐北中学校','西郷小学校':'岐北中学校','網代小学校':'岐北中学校',
    '厚見小学校':'厚見中学校','鷺山小学校':'青山中学校','常磐小学校':'青山中学校',
    '加納西小学校':'陽南中学校','三里小学校':'陽南中学校',
    '七郷小学校':'岐阜西中学校','合渡小学校':'岐阜西中学校',
    '長森南小学校':'長森南中学校','長良小学校':'東長良中学校','長良東小学校':'東長良中学校',
    '且格小学校':'境川中学校','鶉小学校':'境川中学校','柳津小学校':'境川中学校',
}

_GIFU_GIMU_AREAS = {
    '藍川北学園':['岩井','大蔵台','加野','向加野'],
    '藍東学園':['大洞柏台','大洞桐が丘','大洞桜台','大洞紅葉が丘','北山','芥見東山','大洞','大洞西','大洞緑山','芥見南山','コモンヒルズ北山'],
}

# 番地・道路境界で複数校に分かれる代表的な町名。ここは誤判定を避けて自治体確認に回す。
_GIFU_COMPLEX_TOWNS = {
    '日光町','大福町','長森岩戸','下土居','細畑華南','長良','長良福光','長良友瀬','長良奥郷','長良西野前','長良子正賀',
    '白菊町','旦島','一日市場北町','今嶺4丁目','西荘1丁目','西荘2丁目','西荘3丁目','鏡島','鷺山','早田','光町',
    '下奈良4丁目','芥見','石原'
}

def _gifu_candidate_matches_local(local, candidate):
    """岐阜市規則の「○○1丁目」と、入力の「○○1-25」の両方を照合する。"""
    local=_to_ascii_school_text(local)
    candidate=_to_ascii_school_text(candidate)
    if not candidate:
        return False
    if local.startswith(candidate):
        return True
    # 例: 薮田南1丁目 ↔ 薮田南1-25 / 薮田南1－25
    if candidate.endswith('丁目'):
        stem=candidate[:-2]
        return bool(re.match(rf'^{re.escape(stem)}(?:丁目|[-－ー])', local))
    return False

def _gifu_city_school_fallback(address):
    s=normalize_japanese_address_for_school(address)
    if '岐阜市' not in s: return None
    local=s.split('岐阜市',1)[1]

    # 2026年度の義務教育学校（公式別表第3）を先に判定
    for school,stems in _GIFU_GIMU_AREAS.items():
        if any(local.startswith(_to_ascii_school_text(x)) for x in stems):
            return {
                'elementary':f'岐阜市立{school}（前期課程）',
                'junior_high':f'岐阜市立{school}（後期課程）',
                'source':'岐阜市公式通学区域規則（別表第3）'
            }

    # 薮田南1～5丁目は岐阜市公式規則（別表第1）で市橋小学校区。
    # 別表第2により、市橋小学校区は精華中学校区。
    # 「薮田南1-25」のようなハイフン住所でも、外部ページ取得に依存せず確実に判定する。
    # 薮田南1～5丁目は固定公式ルール。番地の有無・ハイフン表記にかかわらず判定する。
    if re.search(r'薮田南[1-5](?:丁目|[-－ー]|$)', local):
        return {
            'elementary':'岐阜市立市橋小学校',
            'junior_high':'岐阜市立精華中学校',
            'source':'岐阜市公式通学区域規則（別表第1・第2）'
        }

    # 茜部は動作確認済みの固定ルールを残す
    akanabe_areas=[
        '茜部大野1丁目','茜部大野2丁目','茜部大川1丁目','茜部大川2丁目',
        '茜部新所1丁目','茜部新所2丁目','茜部新所3丁目','茜部新所4丁目',
        '茜部神清寺1丁目','茜部神清寺2丁目','茜部寺屋敷1丁目','茜部寺屋敷2丁目','茜部寺屋敷3丁目',
        '茜部中島1丁目','茜部中島2丁目','茜部中島3丁目','茜部野瀬1丁目','茜部野瀬2丁目','茜部野瀬3丁目',
        '茜部菱野1丁目','茜部菱野2丁目','茜部菱野3丁目','茜部菱野4丁目','茜部本郷1丁目','茜部本郷2丁目','茜部本郷3丁目',
        '水主町1丁目','水主町2丁目','境川1丁目','境川2丁目','境川3丁目','境川4丁目','境川5丁目','茜部辰新1丁目','茜部辰新2丁目']
    if any(a in s for a in akanabe_areas) or re.search(r'岐阜市茜部(?:[-－ー0-9]|$)',s) or '岐阜市茜町' in s:
        return {'elementary':'岐阜市立茜部小学校','junior_high':'岐阜市立加納中学校','source':'岐阜市公式通学区域規則'}

    rows=_fetch_gifu_school_rows()
    if not rows: return None
    matches=[]
    for school,area,source_url in rows:
        # 旧校名は2026年度の義務教育学校へ移行済みなので通常小学校判定から除外
        if school in ('藍川小学校','芥見東小学校'): continue
        for candidate,complex_flag in _expand_area_candidates(area):
            c=_to_ascii_school_text(candidate)
            if c and _gifu_candidate_matches_local(local,c):
                matches.append((len(c),school,c,complex_flag,source_url)); break
    if not matches: return None
    matches.sort(reverse=True)
    best_len=matches[0][0]
    best=[m for m in matches if m[0]==best_len]
    schools={m[1] for m in best}
    matched_name=best[0][2]
    # 複数校候補、または代表的な番地境界町名は安全のため自動断定しない
    if len(schools)!=1 or any(local.startswith(_to_ascii_school_text(x)) for x in _GIFU_COMPLEX_TOWNS):
        return None
    es=best[0][1]
    jhs=_GIFU_JHS_BY_ES.get(es)
    if not jhs: return None
    return {
        'elementary':f'岐阜市立{es}',
        'junior_high':f'岐阜市立{jhs}',
        'source':'岐阜市公式通学区域規則準拠（岐阜市全域補完・番地境界は要確認）'
    }

def get_school_district_fallback(address):
    # 岐阜市：公式規則準拠の全域補完（安全優先。複雑な番地境界は断定しない）
    fb=_gifu_city_school_fallback(address)
    if fb: return fb

    # 既存の補完参考データ
    s=normalize_japanese_address_for_school(address)
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

def distance_to_segment_m(lat,lon,a,b):
    """地点周辺を平面近似し、線分までのおおよその距離を返す。"""
    scale_x=111320*math.cos(math.radians(lat)); scale_y=110540
    ax,ay=(a[0]-lon)*scale_x,(a[1]-lat)*scale_y
    bx,by=(b[0]-lon)*scale_x,(b[1]-lat)*scale_y
    dx,dy=bx-ax,by-ay
    if dx==0 and dy==0:return math.hypot(ax,ay)
    t=max(0,min(1,-(ax*dx+ay*dy)/(dx*dx+dy*dy)))
    return math.hypot(ax+t*dx,ay+t*dy)

def distance_to_line_geometry_m(lat,lon,geometry):
    typ=geometry.get("type"); coords=geometry.get("coordinates") or []
    lines=[]
    if typ=="LineString": lines=[coords]
    elif typ=="MultiLineString": lines=coords
    elif typ=="Polygon": lines=coords
    elif typ=="MultiPolygon": lines=[ring for poly in coords for ring in poly]
    distances=[distance_to_segment_m(lat,lon,line[i-1],line[i]) for line in lines for i in range(1,len(line))]
    return min(distances) if distances else None

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
            if ("調剤" in name or "薬局" in name) and not any(chain in name for chain in queries[1:]):
                continue
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
            raw=((p.get("datasource") or {}).get("raw") or {})
            operator=p.get("operator") or raw.get("operator") or raw.get("network") or ""
            line=p.get("line") or raw.get("line") or raw.get("route_ref") or ""
            out.append({"name":name,"distance_m":round(d),"walk_min":walking_minutes_estimate(d),"lat":flat,"lon":flon,"operator":operator,"line":line})
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

def split_rail_line(line):
    line=(line or "").strip()
    prefixes=[
        ("名古屋市営地下鉄","名古屋市営地下鉄"),("名古屋市営","名古屋市営地下鉄"),
        ("名鉄","名古屋鉄道"),("近鉄","近畿日本鉄道"),("JR","JR"),
        ("養老鉄道","養老鉄道"),("樽見鉄道","樽見鉄道"),("長良川鉄道","長良川鉄道"),
        ("愛知環状鉄道","愛知環状鉄道"),("名古屋臨海高速鉄道","名古屋臨海高速鉄道"),
        ("東海交通事業","東海交通事業"),("伊勢鉄道","伊勢鉄道"),
    ]
    for prefix,operator in prefixes:
        if line.startswith(prefix):
            remainder=line[len(prefix):].strip()
            return operator,remainder or line
    return "",line

def get_heartrails_stations(lat,lon,limit_count=3):
    """路線名を含む最寄駅を取得する。取得不能時はGeoapifyへ戻す。"""
    try:
        response=requests.get(
            "https://express.heartrails.com/api/json",
            params={"method":"getStations","x":lon,"y":lat},timeout=15,
            headers={"User-Agent":"ATRIS/1.0"})
        response.raise_for_status()
        stations=((response.json() or {}).get("response") or {}).get("station") or []
        if isinstance(stations,dict): stations=[stations]
        out=[]; seen=set()
        for station in stations:
            try: flat=float(station.get("y")); flon=float(station.get("x"))
            except (TypeError,ValueError): continue
            name=str(station.get("name") or "").strip()
            line=str(station.get("line") or "").strip()
            operator,line_name=split_rail_line(line)
            key=(name,line)
            if not name or key in seen: continue
            seen.add(key)
            distance=haversine_distance_m(lat,lon,flat,flon)
            out.append({"name":name,"distance_m":round(distance),"walk_min":walking_minutes_estimate(distance),
                        "lat":flat,"lon":flon,"operator":operator,"line":line_name})
        out.sort(key=lambda item:item["distance_m"])
        return out[:limit_count]
    except Exception:
        return []

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
        return "新しい建物をむやみに増やさないための区域です。建物を建てるには、許可などの条件があります。建てられるかは自治体で確認が必要です。"
    if "市街化区域" in t:
        return "町として整備されている区域です。場所ごとのルールにより、建てられる建物の種類や大きさが決まっています。"
    if "非線引き" in t or "区域区分非設定" in t or ("都市計画区域" in t and "市街化区域" not in t and "市街化調整区域" not in t):
        return "市街化区域と市街化調整区域に分けていない地域です。建物を建てるときは、用途地域や道路などの条件を一つずつ確認します。"
    return "区域区分によって建築や開発の条件が異なります。詳細は自治体で確認してください。"

def perform_search(address, current_lat=None, current_lon=None):
    if current_lat is not None and current_lon is not None:
        lat,lon=float(current_lat),float(current_lon)
        display_address=f"現在地（緯度 {lat:.6f}／経度 {lon:.6f}）"
    else:
        geo=requests.get("https://msearch.gsi.go.jp/address-search/AddressSearch",params={"q":address},timeout=10)
        geo.raise_for_status(); gd=geo.json()
        if not gd: raise ValueError("住所が見つかりませんでした。住所を少し短くしてお試しください。")
        lon,lat=gd[0]["geometry"]["coordinates"]
        display_address=address
    x,y=latlon_to_tile(lat,lon,15)

    codes={}
    api_codes=["XKT001","XKT002","XKT014","XKT003","XKT020","XKT021","XKT022","XKT023","XKT026","XKT027","XKT028","XKT029","XKT030"]
    def fetch_code(code):
        features=get_api_features_neighborhood(code,x,y) if code=="XKT023" else get_api_features(code,x,y)
        return code,(features if code=="XKT030" else find_matching_features(features,lon,lat))
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures=[pool.submit(fetch_code,code) for code in api_codes]
        school_e=pool.submit(get_school_district_features_multizoom,"XKT004",lat,lon)
        school_j=pool.submit(get_school_district_features_multizoom,"XKT005",lat,lon)
        nearby_future=pool.submit(get_nearby_shops,lat,lon)
        drugs_future=pool.submit(get_yahoo_drugstores,lat,lon)
        stations_future=pool.submit(get_heartrails_stations,lat,lon)
        inland_future=pool.submit(get_inland_flood_status,lat,lon)
        for future in as_completed(futures):
            code,value=future.result(); codes[code]=value
        ef,es,_=school_e.result(); jf,js,_=school_j.result()
        nearby,shop_error=nearby_future.result(); drugs,drug_error=drugs_future.result()
        rail_stations=stations_future.result(); inland_flood=inland_future.result()
    em=find_matching_features(ef,lon,lat); jm=find_matching_features(jf,lon,lat)

    if drugs: nearby["drugstore"]=drugs
    if rail_stations: nearby["station"]=rail_stations
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
    residence="区域内（公開されている地図情報による判定）" if in_res else ("公開されている地図情報では着色を確認できません（区域外か自治体確認）" if in_plan else "公開されている地図情報では判定できません（要自治体確認）")

    floods=[]
    for f in codes["XKT026"]:
        p=f.get("properties",{})
        item=(p.get("A31a_202","河川名不明"),flood_rank_text(p.get("A31a_205","")))
        if item not in floods: floods.append(item)

    storm_surges=[]
    for f in codes["XKT027"]:
        p=f.get("properties",{})
        depth=str(p.get("A49_003") or "浸水深不明").strip()
        if depth not in storm_surges: storm_surges.append(depth)

    tsunamis=[]
    for f in codes["XKT028"]:
        p=f.get("properties",{})
        depth=str(p.get("A40_003") or "浸水深不明").strip()
        if depth not in tsunamis: tsunamis.append(depth)

    district_plans=[]
    for f in codes["XKT023"]:
        p=f.get("properties",{})
        name=str(p.get("plan_name") or p.get("plan_type_ja") or "地区計画（名称不明）").strip()
        if name not in district_plans: district_plans.append(name)

    planning_roads=[]
    for f in codes["XKT030"]:
        distance=distance_to_line_geometry_m(lat,lon,f.get("geometry",{}))
        if distance is not None and distance<=50:
            p=f.get("properties",{})
            label=str(p.get("planning_road_ja") or "都市計画道路").strip()
            item=f"{label}の計画線から約{round(distance)}m"
            if item not in planning_roads: planning_roads.append(item)

    embankments=[]
    for f in codes["XKT020"]:
        p=f.get("properties",{})
        name=str(p.get("embankment_classification") or "大規模盛土造成地").strip()
        if name not in embankments: embankments.append(name)

    landslide_prevention=[]
    for code,label in (("XKT021","地すべり防止区域"),("XKT022","急傾斜地崩壊危険区域")):
        for f in codes[code]:
            p=f.get("properties",{})
            name=str(p.get("region_name") or p.get("address") or "名称不明").strip()
            item=f"{label}：{name}"
            if item not in landslide_prevention: landslide_prevention.append(item)

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

    # 岐阜市は国の学区GISより岐阜市公式通学区域規則を優先する。
    # GIS側の更新差で小中学校の組合せが食い違うケースを防ぐため、
    # 公式規則で一意に判定できたときは小学校・中学校とも公式判定で上書きする。
    is_gifu_city = '岐阜市' in normalize_japanese_address_for_school(address)
    if is_gifu_city and fb:
        elementary = elementary_fallback
        junior = junior_fallback
    else:
        elementary=" / ".join(en) if en else (elementary_fallback if fb else ("公開データで判定できません（要自治体確認）" if es>0 else "学区データを取得できません"))
        junior=" / ".join(jn) if jn else (junior_fallback if fb else ("公開データで判定できません（要自治体確認）" if js>0 else "学区データを取得できません"))

    def facility_list(key):
        out=[]
        ranked=[]
        for i,f in enumerate(nearby.get(key,[])):
            route=routes.get((key,i))
            distance=route.get("route_distance_m") if route else f.get("distance_m",10**9)
            ranked.append((distance,i,f,route))
        for _,i,f,r in sorted(ranked,key=lambda row:row[0])[:3]:
            item=dict(f)
            if key=="station":
                parts=[str(item.get("operator") or "").strip(),str(item.get("line") or "").strip(),str(item.get("name") or "").strip()]
                parts=[p for i,p in enumerate(parts) if p and p not in parts[:i]]
                item["name"]=" ".join(parts)
                if item["name"] and not item["name"].endswith("駅"): item["name"]+="駅"
            if r:
                item.update({"distance_text":f"徒歩距離 約{r['route_distance_m']}m","time_text":f"徒歩 約{r['walk_min']}分","route":True})
            else:
                item.update({"distance_text":f"直線距離 約{f['distance_m']}m","time_text":f"徒歩目安 約{f['walk_min']}分","route":False})
            out.append(item)
        return out

    return {
        "address":display_address,
        "searched_at":datetime.now(timezone(timedelta(hours=9))).strftime("%Y年%m月%d日 %H:%M"),
        "area_names":area_names or ["該当データなし"],
        "area_explanation":area_explanation(area_names),
        "uses":uses,
        "fire":" / ".join(fire) if fire else "公開されている地図情報では防火・準防火の該当を確認できません（自治体確認）",
        "residence":residence,
        "floods":floods,
        "storm_surges":storm_surges,
        "tsunamis":tsunamis,
        "inland_flood":inland_flood,
        "sediments":sediments,
        "district_plans":district_plans,
        "planning_roads":planning_roads,
        "embankments":embankments,
        "landslide_prevention":landslide_prevention,
        "facilities":{
            "コンビニ":facility_list("convenience"),
            "スーパー":facility_list("supermarket"),
            "ドラッグストア":facility_list("drugstore"),
            "最寄駅":facility_list("station"),
        },
        "elementary":elementary,
        "junior":junior,
        "history_map_url":f"https://maps.gsi.go.jp/#16/{lat}/{lon}/&base=std&ls=std&disp=1",
        "legal_checks":[
            {"name":"景観法","status":"公開データでは判定できません","note":"自治体の景観計画・届出対象規模を確認"},
            {"name":"宅地造成及び特定盛土等規制法","status":"公開データでは判定できません","note":"大規模盛土造成地マップとは別制度。規制区域図と行為内容を確認"},
            {"name":"水防法","status":"該当する可能性あり" if (floods or storm_surges) else "公開データでは判定できません","note":"自治体の水害ハザードマップで最終確認"},
            {"name":"砂防法","status":"該当する可能性あり" if sediments else "公開データでは判定できません","note":"砂防指定地は別途自治体資料で確認"},
            {"name":"地すべり等防止法","status":"該当する可能性あり" if any("地すべり" in x for x in landslide_prevention) else "公開データでは判定できません","note":"指定区域資料を確認"},
            {"name":"急傾斜地法","status":"該当する可能性あり" if any("急傾斜地" in x for x in landslide_prevention) else "公開データでは判定できません","note":"指定区域資料を確認"},
            {"name":"河川法","status":"公開データでは判定できません","note":"河川区域・河川保全区域を確認"},
            {"name":"海岸法・港湾法","status":"公開データでは判定できません","note":"海岸保全区域・港湾区域等を確認"},
            {"name":"農地法","status":"公開されている情報だけでは判定できません","note":"土地の現在の状態、登記の地目、農地の区分を自治体で確認"},
            {"name":"森林法","status":"公開されている情報だけでは判定できません","note":"森林法の対象になる土地かを自治体で確認"},
            {"name":"文化財保護法","status":"公開データでは判定できません","note":"埋蔵文化財包蔵地等を自治体で確認"},
            {"name":"航空法","status":"公開データでは判定できません","note":"空港周辺の高さ制限等を確認"},
        ],
    }

HTML = r'''<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0f4c81">
<title>ATRIS｜不動産らくらく調査</title>
<style>
:root{--bg:#f3f6f9;--card:#fff;--ink:#17212b;--muted:#657381;--accent:#083b69;--accent2:#0876bd;--warn:#9a5b00;--danger:#a52820;--soft:#eef5fa}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,sans-serif;background:var(--bg);color:var(--ink)}
header{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;padding:16px 16px 22px;position:sticky;top:0;z-index:5;box-shadow:0 2px 10px #0002}
.wrap{max-width:920px;margin:auto;padding:14px}.brand{display:flex;align-items:center;gap:11px;margin-bottom:8px}.brandmark{width:48px;height:48px;filter:drop-shadow(0 3px 8px #001b3555)}.brandname{font-size:27px;font-weight:800;letter-spacing:.12em;line-height:1}.brandline{font-size:11px;opacity:.9;letter-spacing:.04em;margin-top:4px}h1{font-size:14px;margin:0 0 8px;font-weight:600}.subtitle{font-size:13px;opacity:.92;margin-bottom:12px}.scope{display:inline-block;font-size:11px;background:#ffffff24;padding:4px 8px;border-radius:999px;margin:0 4px 10px 0}.modebar{display:flex;gap:6px;margin-bottom:10px}.mode{color:#fff;text-decoration:none;border:1px solid #ffffff66;border-radius:999px;padding:6px 12px;font-size:12px}.mode.active{background:#fff;color:var(--accent);font-weight:700}.tools{display:flex;justify-content:flex-end;gap:8px;margin:12px 0}.toolbtn{border:1px solid #bfd0dd;background:#fff;color:var(--accent);border-radius:9px;padding:9px 13px;font-weight:700}
form{display:flex;gap:8px}.address{flex:1;padding:13px 14px;border:0;border-radius:10px;font-size:16px;min-width:0}.btn{padding:0 18px;border:0;border-radius:10px;background:#fff;color:var(--accent);font-weight:700;font-size:15px;white-space:nowrap}.location-btn{width:100%;margin-top:8px;padding:9px;border:1px solid #ffffff88;border-radius:9px;background:#ffffff18;color:#fff;font-weight:700}
.card{background:#fff;border-radius:14px;padding:17px;margin:12px 0;box-shadow:0 2px 12px #15283b12;border:1px solid #e9eef2}.card h2{font-size:18px;margin:0 0 12px;color:var(--accent)}.card h3{font-size:15px;margin:16px 0 7px}
.row{display:grid;grid-template-columns:128px 1fr;gap:8px;padding:7px 0;border-bottom:1px solid #edf1f4}.row:last-child{border-bottom:0}.label{color:var(--muted);font-size:14px}.value{font-weight:600}.value.warn{color:var(--danger)}
.desc{background:#f6f9fb;border-left:4px solid #8bb7d4;padding:10px 12px;border-radius:8px;line-height:1.7;font-size:14px}.facility{padding:10px 0;border-bottom:1px solid #edf1f4}.facility:last-child{border-bottom:0}.facility b{display:block;margin-bottom:4px}.meta{font-size:13px;color:var(--muted)}
.notice{font-size:12px;line-height:1.65;color:var(--muted)}.error{background:#fff1f0;border:1px solid #ffd1cc;color:#8c2b20;padding:14px;border-radius:12px;margin:12px 0}.spinner{display:none;margin-left:8px}.loading .spinner{display:inline}.loading .btn{opacity:.7}.loading-screen{display:none;position:fixed;inset:0;background:#052f55ee;color:#fff;z-index:99;align-items:center;justify-content:center;text-align:center;padding:24px}.loading-screen.show{display:flex}.loader-logo{font-size:34px;font-weight:900;letter-spacing:.16em}.loader-ring{width:42px;height:42px;border:4px solid #ffffff55;border-top-color:#fff;border-radius:50%;margin:22px auto;animation:spin .9s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.loan-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.loan-grid label{font-size:12px;color:var(--muted)}.loan-grid input{width:100%;margin-top:4px;padding:10px;border:1px solid #d7e0e7;border-radius:8px;font-size:16px}.loan-result{margin-top:12px;background:var(--soft);padding:12px;border-radius:10px;font-weight:700}.history-link{display:inline-block;margin-top:10px;color:var(--accent2);font-weight:700;text-decoration:none}footer{padding:12px 4px 32px;font-size:11px;color:#73808c;line-height:1.7}
@media(max-width:600px){header{padding-top:calc(14px + env(safe-area-inset-top))}.wrap{padding:10px}form{display:block}.address{width:100%;margin-bottom:8px}.btn{width:100%;height:46px}.card{border-radius:12px;padding:15px;margin:10px 0}.row{grid-template-columns:1fr;gap:2px}.label{font-size:12px}.value{font-size:15px}.brandname{font-size:24px}}
@media print{header{position:static;background:#fff;color:#123;box-shadow:none;border-bottom:2px solid #0f4c81}.modebar,form,.location-btn,.tools,.loading-screen{display:none!important}.card{box-shadow:none;break-inside:avoid}.wrap{max-width:none}.notice{color:#4d5964}}
</style>
</head>
<body>
<header><div class="wrap" style="padding:0">
<div class="brand"><svg class="brandmark" viewBox="0 0 64 64" role="img" aria-label="ATRIS"><path fill="#fff" d="M31 3 7 48h12l5-10h15l5 10h13L34 3h-3Zm1 15 6 13H26l6-13Z"/><path fill="#7fc8f0" d="M23 51V38h6v13h4V31h6v20h4V25h6v26c4-2 7-4 9-7-2 11-12 17-25 17S9 56 6 47c5 4 14 6 26 6 9 0 17-1 23-4-5 7-13 10-23 10-11 0-20-3-24-8 4 2 9 3 15 4Z"/><circle cx="51" cy="40" r="7" fill="#fff"/><circle cx="51" cy="40" r="3" fill="#0876bd"/></svg><div><div class="brandname">ATRIS</div><div class="brandline">Real Estate Investigation System</div></div></div>
<h1>不動産らくらく調査</h1>
<div class="modebar"><a class="mode {% if mode == 'sales' %}active{% endif %}" href="/?mode=sales">営業向け</a><a class="mode {% if mode == 'internal' %}active{% endif %}" href="/?mode=internal">鳥内さん用</a></div>
<div class="scope">愛知・岐阜・三重｜{{ '詳しい調査画面' if mode == 'internal' else '営業現場向け' }}</div>
<div class="scope" style="font-size:11px;opacity:.8">版: {{ build_version }}</div>
<div class="subtitle">土地・建築制限／ハザード／学区／生活情報をまとめて確認</div>
<form method="get" action="/" id="searchForm">
<input type="hidden" name="mode" value="{{ mode }}">
<input class="address" name="address" value="{{ address|e }}" placeholder="例：名古屋市中区新栄2-46-1" autocomplete="street-address" required>
<button class="btn" type="submit">この住所を調査 <span class="spinner">…</span></button>
</form><button class="location-btn" type="button" id="locationBtn">📍 現在地から調査</button></div></header>
<main class="wrap">
{% if error %}<div class="error">{{ error }}</div>{% endif %}
{% if r %}
{% if mode == 'internal' %}<div class="tools"><button class="toolbtn" type="button" onclick="window.print()">🖨 調査結果を印刷</button></div>{% endif %}
<div class="card"><h2>📍 物件調査結果</h2><div class="row"><div class="label">所在地</div><div class="value">{{ r.address }}</div></div>{% if mode == 'internal' %}<div class="row"><div class="label">調査日時</div><div class="value">{{ r.searched_at }}</div></div>{% endif %}</div>
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
<div class="card"><h2>🕰 土地の履歴（参考）</h2>
<div class="desc">昔の地図や航空写真を見ると、この土地や周りが以前どのように使われていたかを確認できます。</div>
<a class="history-link" href="{{ r.history_map_url }}" target="_blank" rel="noopener">国土地理院の地図で土地の成り立ちや昔の写真を見る ↗</a>
<div class="notice" style="margin-top:10px">※昔の土地利用だけで、現在の土地が安全か危険かを決めることはできません。建物を建てるときは、地盤調査なども合わせて確認すると安心です。</div>
</div>
<div class="card"><h2>🌊 ハザード情報</h2>
{% if r.floods %}<div class="row"><div class="label">洪水浸水想定</div><div class="value warn">⚠ 区域内</div></div>{% for river,depth in r.floods %}<div class="row"><div class="label">河川・浸水深</div><div class="value">{{ river }} ／ {{ depth }}</div></div>{% endfor %}
{% else %}<div class="row"><div class="label">洪水浸水想定</div><div class="value">公開データ上の該当なし</div></div>{% endif %}
{% if r.sediments %}<div class="row"><div class="label">土砂災害</div><div class="value warn">⚠ 区域内</div></div>{% for s in r.sediments %}<div class="row"><div class="label">{{ s.phenomenon }}</div><div class="value">{{ s.type }}{% if s.name %} ／ {{ s.name }}{% endif %}</div></div>{% endfor %}
{% else %}<div class="row"><div class="label">土砂災害</div><div class="value">公開データ上の該当なし</div></div>{% endif %}
<div class="row"><div class="label">内水</div><div class="value {% if r.inland_flood.matched %}warn{% endif %}">{{ r.inland_flood.status }}</div></div>
{% if r.storm_surges %}<div class="row"><div class="label">高潮浸水想定</div><div class="value warn">⚠ 区域内</div></div>{% for depth in r.storm_surges %}<div class="row"><div class="label">想定浸水深</div><div class="value">{{ depth }}</div></div>{% endfor %}{% else %}<div class="row"><div class="label">高潮浸水想定</div><div class="value">公開データ上の該当なし</div></div>{% endif %}
{% if r.tsunamis %}<div class="row"><div class="label">津波浸水想定</div><div class="value warn">⚠ 区域内</div></div>{% for depth in r.tsunamis %}<div class="row"><div class="label">想定浸水深</div><div class="value">{{ depth }}</div></div>{% endfor %}{% else %}<div class="row"><div class="label">津波浸水想定</div><div class="value">公開データ上の該当なし</div></div>{% endif %}
<div class="notice">※「公開データ上の該当なし」は安全を保証するものではありません。</div>
</div>
<div class="card"><h2>🏫 学区情報</h2>
<div class="row"><div class="label">小学校区</div><div class="value">{{ r.elementary }}</div></div>
<div class="row"><div class="label">中学校区</div><div class="value">{{ r.junior }}</div></div>
<div class="notice">※学区は参考情報です。岐阜市は公式通学区域規則準拠の全域補完を行います。番地・道路境界など複雑な区域は誤判定防止のため「要自治体確認」と表示します。判定できない場合に学校名を推測せず「要自治体確認」と表示します。最新の指定校・通学区域は各自治体で確認してください。</div>
</div>
<div class="card"><h2>🛒 生活情報</h2>
{% for label,items in r.facilities.items() %}<h3>{{ label }}</h3>
{% if items %}{% for f in items %}<div class="facility"><b>{{ loop.index }}. {{ f.name }}</b><div class="meta">{{ f.distance_text }}　{{ f.time_text }}{% if not f.route %}（概算）{% endif %}</div></div>{% endfor %}
{% else %}<div class="meta">登録データなし</div>{% endif %}{% endfor %}
</div>
{% if mode == 'internal' %}<div class="card"><h2>📋 詳細調査・法令確認</h2>
<div class="row"><div class="label">居住誘導区域</div><div class="value">{{ r.residence }}</div></div>
<div class="row"><div class="label">高度地区</div><div class="value">自動判定準備中（自治体確認）</div></div>
<div class="row"><div class="label">都市計画道路</div><div class="value {% if r.planning_roads %}warn{% endif %}">{{ r.planning_roads|join(' / ') if r.planning_roads else '公開データ上、計画線から50m以内の該当なし' }}</div></div>
<div class="row"><div class="label">大規模盛土造成地</div><div class="value {% if r.embankments %}warn{% endif %}">{{ r.embankments|join(' / ') if r.embankments else '公開データ上の該当なし' }}</div></div>
<div class="row"><div class="label">地区計画</div><div class="value {% if r.district_plans %}warn{% endif %}">{{ r.district_plans|join(' / ') if r.district_plans else '公開されている地図情報では確認できません（自治体確認）' }}</div></div>
<div class="row"><div class="label">地すべり・急傾斜地</div><div class="value {% if r.landslide_prevention %}warn{% endif %}">{{ r.landslide_prevention|join(' / ') if r.landslide_prevention else '公開データ上の該当なし' }}</div></div>
<div class="row"><div class="label">景観等</div><div class="value">必要に応じ自治体確認</div></div>
<div class="notice">※都市計画道路は公開された計画線との距離による一次確認です。道路幅を含む区域内外は自治体資料で確認してください。</div>
<h3>都市計画法・建築基準法以外の法令</h3>
{% for law in r.legal_checks %}<div class="row"><div class="label">{{ law.name }}</div><div><div class="value {% if '可能性' in law.status %}warn{% endif %}">{{ law.status }}</div><div class="notice">{{ law.note }}</div></div></div>{% endfor %}
</div>{% endif %}
<div class="card"><h2>🏦 住宅ローン概算</h2>
<div class="loan-grid"><label>借入金額（万円）<input id="loanAmount" type="number" value="3000" min="0"></label><label>年利（％）<input id="loanRate" type="number" value="1" min="0" step="0.01"></label><label>返済期間（年）<input id="loanYears" type="number" value="35" min="1"></label><label>ボーナス1回の返済額（万円・年2回）<input id="loanBonus" type="number" value="0" min="0" step="0.1"></label></div>
<div class="loan-result" id="loanResult">入力すると毎月の返済額を概算表示します。</div><div class="notice">※元利均等返済による概算です。実際の返済額は金融機関の商品・金利・諸条件により異なります。</div></div>
<div class="card"><h2>情報源・注意事項</h2><div class="notice">
・用途地域等：不動産情報ライブラリ（国土交通省）<br>・防火・準防火：XKT014 ／ 居住誘導区域：XKT003<br>・洪水：XKT026 ／ 内水：重ねるハザードマップ ／ 高潮：XKT027 ／ 津波：XKT028 ／ 土砂災害：XKT029<br>・地区計画：XKT023 ／ 大規模盛土造成地：XKT020<br>・地すべり防止区域：XKT021 ／ 急傾斜地崩壊危険区域：XKT022<br>・学区：不動産情報ライブラリの公開データを基本とし、岐阜市は公式通学区域規則準拠の全域補完（複雑な番地境界は要自治体確認）<br>・公開されている地図情報で判定できない場合は「指定なし」「区域外」と断定しません。<br>・契約・重要事項説明に使用する場合は、必ず最新の行政情報を確認してください。
</div></div>{% endif %}
<footer>東海三県（愛知・岐阜・三重）の営業利用を優先して整備中です。<br>コンビニ・スーパー：Geoapify Places API ／ ドラッグストア：Yahoo!ローカルサーチAPI ／ 駅：HeartRails Express<br>徒歩経路：OpenStreetMap道路データを利用する公開ルートサービス（取得不可時は概算）<br>© OpenStreetMap contributors　／　Web Services by Yahoo! JAPAN<br>Developed by J. Toriuchi</footer>
</main>
<div class="loading-screen" id="loadingScreen"><div><div class="loader-logo">ATRIS</div><div class="loader-ring"></div><div>物件情報を調査しています。<br>そのまま少々お待ちください。</div></div></div>
<script>
document.getElementById('searchForm').addEventListener('submit',function(){this.classList.add('loading');this.querySelector('.btn').disabled=true;document.getElementById('loadingScreen').classList.add('show');});
document.getElementById('locationBtn').addEventListener('click',function(){const btn=this;btn.disabled=true;btn.textContent='現在地を確認しています…';if(!navigator.geolocation){alert('この端末では現在地を取得できません。');btn.disabled=false;return}navigator.geolocation.getCurrentPosition(function(pos){const form=document.getElementById('searchForm');['lat','lon'].forEach(function(name){let el=document.createElement('input');el.type='hidden';el.name=name;el.value=name==='lat'?pos.coords.latitude:pos.coords.longitude;form.appendChild(el)});form.querySelector('.address').required=false;form.submit();document.getElementById('loadingScreen').classList.add('show');},function(){alert('現在地を取得できませんでした。位置情報の利用を許可してください。');btn.disabled=false;btn.textContent='📍 現在地から調査';},{enableHighAccuracy:true,timeout:10000});});
function calcLoan(){const a=Number(document.getElementById('loanAmount').value)*10000,b=Number(document.getElementById('loanBonus').value)*10000,y=Number(document.getElementById('loanYears').value),rate=Number(document.getElementById('loanRate').value)/1200,n=y*12;if(!a||!y){return}let bonusPV=0;for(let month=6;month<=n;month+=6){bonusPV+=b/Math.pow(1+rate,month)}if(bonusPV>=a){document.getElementById('loanResult').textContent='ボーナス返済額が大きすぎます。金額を小さくしてください。';return}const monthlyPrincipal=a-bonusPV,pay=rate?monthlyPrincipal*rate*Math.pow(1+rate,n)/(Math.pow(1+rate,n)-1):monthlyPrincipal/n;document.getElementById('loanResult').innerHTML='通常月　約 '+Math.round(pay).toLocaleString()+'円'+(b?'<br>ボーナス月　約 '+Math.round(pay+b).toLocaleString()+'円<br><span style="font-weight:400;font-size:12px">（通常月の返済＋ボーナス返済 '+Math.round(b).toLocaleString()+'円／年2回）</span>':'');}
['loanAmount','loanRate','loanYears','loanBonus'].forEach(id=>document.getElementById(id).addEventListener('input',calcLoan));calcLoan();
</script>
</body></html>'''

@app.route("/")
def index():
    address=(request.args.get("address") or "").strip()
    current_lat=request.args.get("lat")
    current_lon=request.args.get("lon")
    mode=(request.args.get("mode") or "sales").strip()
    if mode not in ("sales","internal"): mode="sales"
    result=None; error=None
    if address or (current_lat and current_lon):
        try: result=perform_search(address,current_lat,current_lon)
        except Exception as e: error=str(e)
    return render_template_string(HTML,address=address,r=result,error=error,build_version=BUILD_VERSION,mode=mode)

if __name__=="__main__":
    print("ATRIS（不動産らくらく調査）Web版")
    print("PC: http://127.0.0.1:5000")
    print("スマホ: 同じWi-Fi内で http://このPCのIPアドレス:5000")
    app.run(host="0.0.0.0",port=5000,debug=False)
