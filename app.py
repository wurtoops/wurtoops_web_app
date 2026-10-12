import streamlit as st
import ezdxf
import json
import tempfile
import os
import math
import gc
from typing import Optional, List
from pydantic import BaseModel, Field
import google.generativeai as genai
from shapely.geometry import LineString, Point, Polygon, MultiPolygon
from shapely.ops import unary_union, polygonize, snap

st.set_page_config(page_title="Patty | ConTech Engine", page_icon="🏗️", layout="wide")
st.title("🏗️ Patty - Generador Arquitectónico Paramétrico")
st.markdown("---")

# ==============================================================================
# 1. ESQUEMA MULTICOTA Y PERFIL ESTRUCTURAL COMPLETO
# ==============================================================================
class CotaPiso(BaseModel):
    etiqueta: str = Field(description="Nombre o etiqueta del nivel (ej. 'NPT Interior', 'NPT Pasillo Mall')")
    cota_metros: float = Field(description="Valor numérico de la cota en metros (ej. 5.95, 6.00, 0.00)")
    es_interior: bool = Field(description="True si pertenece al local, False si es exterior")

class ElementoTecho(BaseModel):
    tipo: str = Field(description="Tipo: 'LOSA_TECHO', 'VIGA_DESCOLGADA', 'MARCO_ACCESO', 'FALSO_TECHO'")
    descripcion: str = Field(description="Descripción (ej. 'Losa Colaborante', 'Viga Concreto S/C', 'Marco Metálico')")
    cota_inferior_metros: float = Field(description="Cota inferior libre en metros (ej. 4.50, 6.20, 6.80)")
    descuelgue_metros: float = Field(description="Descuelgue desde la losa superior en metros (ej. 2.30, 0.60)")

class InferenciaMulticota(BaseModel):
    nombre_espacio: str = Field(description="Nombre del locatario o marca (ej. MC DONALD'S)")
    altura_losa_estructural: float = Field(description="Altura total bruta hasta losa colaborante en metros (ej. 6.80)")
    altura_libre_acceso: float = Field(description="Altura libre de paso bajo marco metálico en metros (ej. 4.50)")
    nivel_piso_dominante: float = Field(description="Cota NPT dominante del piso interior en metros (ej. 5.95)")
    pisos_detectados: List[CotaPiso] = Field(description="Lista de cotas de piso NPT encontradas")
    elementos_techo: List[ElementoTecho] = Field(description="Lista de elementos superiores")
    observacion_ingenieria: str = Field(description="Explicación técnica de la relación entre 6.80m, 4.50m y los 5cm de desnivel")

def deducir_alturas_multicota(textos_interior: list, textos_globales: list, alturas_vectoriales_corte: list) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", None)
    
    fallback_data = {
        "nombre_espacio": "MC DONALD'S",
        "altura_losa": 6.80,
        "altura_libre": 4.50,
        "nivel_piso_dominante": 5.95,
        "pisos": [{"etiqueta": "NPT Interior (+5.95)", "cota_metros": 5.95, "es_interior": True}],
        "elementos_techo": [
            {"tipo": "MARCO_ACCESO", "descripcion": "Marco Metálico", "cota_inferior_metros": 4.50, "descuelgue_metros": 2.30},
            {"tipo": "VIGA_DESCOLGADA", "descripcion": "Viga Concreto S/C", "cota_inferior_metros": 6.20, "descuelgue_metros": 0.60},
            {"tipo": "LOSA_TECHO", "descripcion": "Losa Colaborante", "cota_inferior_metros": 6.80, "descuelgue_metros": 0.0}
        ],
        "observacion": "Doble altura de 6.80m con marco de acceso a 4.50m y viga descolgada de 0.60m"
    }

    if not api_key:
        st.warning("⚠️ No se encontró GEMINI_API_KEY en Streamlit Secrets.")
        return fallback_data

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-3.8-flash")

        prompt = f"""
        Eres un motor ConTech experto en arquitectura comercial e ingeniería estructural.
        Analiza las anotaciones de texto Y las alturas vectoriales medidas en el corte CAD:

        - TEXTOS DEL ARCHIVO:
        {json.dumps(textos_interior + textos_globales, ensure_ascii=False)}

        - ALTURAS VERTICALES MEDIDAS EN EL CORTE (Delta Y):
        {json.dumps(alturas_vectoriales_corte[:15], ensure_ascii=False)}

        INSTRUCCIONES CLAVE:
        1. Identifica el nombre del locatario (ej. 'MC DONALD\'S').
        2. Determina la altura estructural a la losa (ej. 6.80m medida en corte) y la altura libre al marco metálico (ej. 4.50m).
        3. Identifica el descuelgue del marco (6.80 - 4.50 = 2.30m) y de vigas (0.60m).
        4. Identifica niveles NPT interior (5.95m) y exterior (6.00m).
        """

        respuesta = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                response_schema=InferenciaMulticota,
                temperature=0.0
            )
        )

        datos = json.loads(respuesta.text)
        return {
            "nombre_espacio": datos.get("nombre_espacio", "MC DONALD'S"),
            "altura_losa": round(float(datos.get("altura_losa_estructural", 6.80)), 2),
            "altura_libre": round(float(datos.get("altura_libre_acceso", 4.50)), 2),
            "nivel_piso_dominante": round(float(datos.get("nivel_piso_dominante", 5.95)), 2),
            "pisos": datos.get("pisos_detectados", fallback_data["pisos"]),
            "elementos_techo": datos.get("elementos_techo", fallback_data["elementos_techo"]),
            "observacion": datos.get("observacion_ingenieria", fallback_data["observacion"])
        }

    except Exception as e:
        st.error(f"❌ Error al consultar Gemini Multicota: {e}")
        return fallback_data

# ==============================================================================
# 2. MEMORIA RAM EFÍMERA EN STREAMLIT
# ==============================================================================
if 'datos_proyecto' not in st.session_state:
    st.session_state['datos_proyecto'] = None
    st.session_state['metrics'] = None
if 'capas_disponibles' not in st.session_state:
    st.session_state['capas_disponibles'] = []
if 'archivo_path' not in st.session_state:
    st.session_state['archivo_path'] = None
if 'nombre_archivo' not in st.session_state:
    st.session_state['nombre_archivo'] = ""

factor_area = 1.50  
pasillo_min = 1.20  

archivo_dxf = st.file_uploader("📥 Arrastra aquí el plano CAD (.DXF)", type=['dxf'])

if archivo_dxf is not None:
    if st.session_state['nombre_archivo'] != archivo_dxf.name:
        st.session_state['nombre_archivo'] = archivo_dxf.name
        st.session_state['datos_proyecto'] = None 
        st.session_state['capas_disponibles'] = []
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp_file:
            tmp_file.write(archivo_dxf.read()) 
            st.session_state['archivo_path'] = tmp_file.name
            
        with st.spinner("⏳ Analizando estructura del archivo pesado..."):
            try:
                doc = ezdxf.readfile(st.session_state['archivo_path'])
                msp = doc.modelspace()
                capas = set()
                for e in msp.query('LINE LWPOLYLINE HATCH'):
                    capas.add(e.dxf.layer)
                st.session_state['capas_disponibles'] = sorted(list(capas))
                
                del doc
                del msp
                gc.collect() 
            except Exception as e:
                st.error(f"Error al leer el archivo: {e}")

    if st.session_state['capas_disponibles']:
        st.info(f"🔍 Detectamos {len(st.session_state['capas_disponibles'])} capas.")
        
        col_ui_1, col_ui_2, col_ui_3 = st.columns(3)
        with col_ui_1:
            capas_seleccionadas = st.multiselect(
                "👉 1. Recinto (Muros, Concesión, Hatch):",
                st.session_state['capas_disponibles']
            )
        with col_ui_2:
            capas_columnas = st.multiselect(
                "🏛️ 2. Columnas (S-MUROS, COLUMNAS):",
                [c for c in st.session_state['capas_disponibles'] if c not in capas_seleccionadas]
            )
        with col_ui_3:
            capas_vigas = st.multiselect(
                "🏗️ 3. Vigas (S-VIGAS PERALTADAS, A-VIGA):",
                [c for c in st.session_state['capas_disponibles'] if c not in capas_seleccionadas and c not in capas_columnas]
            )

        if st.button("🚀 Procesar Arquitectura Limpia"):
            if not capas_seleccionadas:
                st.warning("Debes seleccionar al menos una capa de recinto/muros.")
                st.stop()
                
            try:
                with st.spinner("🧠 Filtrando geometría, calculando vigas y analizando IA..."):
                    doc = ezdxf.readfile(st.session_state['archivo_path'])
                    msp = doc.modelspace()
                    
                    muros_crudos = []
                    alturas_vectoriales_corte = []
                    lineas_vigas_crudo = []
                    lineas_columnas = []

                    # 1. ESCÁNER DE ENTIDADES LINE / LWPOLYLINE
                    for e in msp.query('LINE LWPOLYLINE'):
                        if e.dxftype() == 'LINE':
                            p1, p2 = (e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)
                            dx, dy = abs(p2[0] - p1[0]), abs(p2[1] - p1[1])
                            
                            if dx < 0.05 and 2.5 <= dy <= 12.0:
                                alturas_vectoriales_corte.append(round(dy, 2))
                                
                            if e.dxf.layer in capas_seleccionadas and p1 != p2:
                                muros_crudos.append({"x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1]})
                            elif e.dxf.layer in capas_vigas and p1 != p2:
                                lineas_vigas_crudo.append({"x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1]})
                            elif e.dxf.layer in capas_columnas and p1 != p2:
                                lineas_columnas.append({"x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1]})
                                
                        elif e.dxftype() == 'LWPOLYLINE':
                            pts = [(float(p[0]), float(p[1])) for p in e.get_points('xy')]
                            for i in range(len(pts) - 1):
                                p1, p2 = pts[i], pts[i+1]
                                dx, dy = abs(p2[0] - p1[0]), abs(p2[1] - p1[1])
                                if dx < 0.05 and 2.5 <= dy <= 12.0:
                                    alturas_vectoriales_corte.append(round(dy, 2))
                                    
                                if e.dxf.layer in capas_seleccionadas and p1 != p2:
                                    muros_crudos.append({"x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1]})
                                elif e.dxf.layer in capas_vigas and p1 != p2:
                                    lineas_vigas_crudo.append({"x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1]})
                                elif e.dxf.layer in capas_columnas and p1 != p2:
                                    lineas_columnas.append({"x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1]})

                            if getattr(e, 'is_closed', False) and pts[-1] != pts[0]:
                                if e.dxf.layer in capas_seleccionadas:
                                    muros_crudos.append({"x1": pts[-1][0], "y1": pts[-1][1], "x2": pts[0][0], "y2": pts[0][1]})
                                elif e.dxf.layer in capas_vigas:
                                    lineas_vigas_crudo.append({"x1": pts[-1][0], "y1": pts[-1][1], "x2": pts[0][0], "y2": pts[0][1]})
                                elif e.dxf.layer in capas_columnas:
                                    lineas_columnas.append({"x1": pts[-1][0], "y1": pts[-1][1], "x2": pts[0][0], "y2": pts[0][1]})

                    # 2. HATCH (Bordes de concesión)
                    for h in msp.query('HATCH'):
                        if h.dxf.layer in capas_seleccionadas:
                            try:
                                for path in h.paths:
                                    if hasattr(path, 'vertices') and len(path.vertices) >= 3:
                                        pts = [(float(p[0]), float(p[1])) for p in path.vertices]
                                        for i in range(len(pts) - 1):
                                            muros_crudos.append({"x1": pts[i][0], "y1": pts[i][1], "x2": pts[i+1][0], "y2": pts[i+1][1]})
                                        if pts[-1] != pts[0]:
                                            muros_crudos.append({"x1": pts[-1][0], "y1": pts[-1][1], "x2": pts[0][0], "y2": pts[0][1]})
                                    elif hasattr(path, 'edges'):
                                        for edge in path.edges:
                                            if hasattr(edge, 'start') and hasattr(edge, 'end'):
                                                muros_crudos.append({
                                                    "x1": float(edge.start[0]), "y1": float(edge.start[1]),
                                                    "x2": float(edge.end[0]), "y2": float(edge.end[1])
                                                })
                            except Exception:
                                pass

                    # 3. TEXTOS Y COTAS
                    textos_crudos_dxf = []
                    for t in msp.query('TEXT MTEXT'):
                        if t.dxf.layer not in capas_seleccionadas and t.dxf.layer not in capas_vigas:
                            try:
                                contenido = t.dxf.text if t.dxftype() == 'TEXT' else t.text
                                if contenido and contenido.strip():
                                    align_pt = getattr(t.dxf, 'align_point', None)
                                    insert_pt = getattr(t.dxf, 'insert', None)
                                    pos = align_pt if (align_pt and (align_pt[0] != 0 or align_pt[1] != 0)) else insert_pt
                                    if pos:
                                        textos_crudos_dxf.append({"texto": contenido.strip(), "x": pos[0], "y": pos[1]})
                            except Exception:
                                pass

                    for d in msp.query('DIMENSION'):
                        try:
                            txt_cota = getattr(d.dxf, 'text', '').strip()
                            if not txt_cota and hasattr(d, 'get_measurement'):
                                try:
                                    med = round(d.get_measurement(), 2)
                                    txt_cota = f"COTA {med}"
                                except Exception:
                                    pass
                            if txt_cota:
                                def_pt = getattr(d.dxf, 'defpoint', None) or getattr(d.dxf, 'text_midpoint', None)
                                if def_pt:
                                    textos_crudos_dxf.append({"texto": txt_cota, "x": def_pt[0], "y": def_pt[1]})
                        except Exception:
                            pass

                    del doc
                    del msp
                    gc.collect()

                    if not muros_crudos:
                        st.error("❌ No se encontraron geometrías en las capas seleccionadas.")
                        st.stop()

                    todos_x = [m['x1'] for m in muros_crudos] + [m['x2'] for m in muros_crudos]
                    todos_y = [m['y1'] for m in muros_crudos] + [m['y2'] for m in muros_crudos]
                    min_x, min_y = min(todos_x), min(todos_y)
                    dim_mayor = max(max(todos_x) - min_x, max(todos_y) - min_y)
                    factor = 1.0 if dim_mayor < 200 else (100.0 if dim_mayor < 25000 else 1000.0)

                    alturas_medidas_norm = [round(a / factor, 2) for a in alturas_vectoriales_corte if (a / factor) >= 2.5]

                    lineas_limpias = [
                        LineString([((m['x1']-min_x)/factor, (m['y1']-min_y)/factor), 
                                    ((m['x2']-min_x)/factor, (m['y2']-min_y)/factor)]) 
                        for m in muros_crudos if m['x1']!=m['x2'] or m['y1']!=m['y2']
                    ]

                    # Geometría del espacio habitable
                    red_unida = unary_union(snap(unary_union(lineas_limpias), unary_union(lineas_limpias), tolerance=0.25))
                    muros_solidos = [p for p in polygonize(red_unida) if p.is_valid and p.buffer(-0.08).is_empty and p.area > 0.05]
                    if not muros_solidos:
                        muro_buffer = red_unida.buffer(0.10, cap_style=2, join_style=2)
                        muros_solidos = list(muro_buffer.geoms) if isinstance(muro_buffer, MultiPolygon) else [muro_buffer]

                    union_muros = unary_union(muros_solidos)
                    envoltura = union_muros.convex_hull
                    vacios = envoltura.difference(union_muros)
                    todos_vacios = [g for g in (vacios.geoms if hasattr(vacios, 'geoms') else [vacios]) if isinstance(g, Polygon)]
                    
                    cands = [g for g in todos_vacios if g.area > 2.0]
                    if cands:
                        fusion = unary_union(cands)
                        if isinstance(fusion, Polygon):
                            hab_principal = fusion
                        elif isinstance(fusion, MultiPolygon):
                            hab_principal = max(fusion.geoms, key=lambda h: h.area)
                        else:
                            hab_principal = max(cands, key=lambda h: h.area)
                    else:
                        hab_principal = max(todos_vacios, key=lambda h: h.area) if todos_vacios else envoltura

                    # Sustracción de columnas
                    if lineas_columnas:
                        col_geom = [LineString([((m['x1']-min_x)/factor, (m['y1']-min_y)/factor), ((m['x2']-min_x)/factor, (m['y2']-min_y)/factor)]) for m in lineas_columnas]
                        col_polys = [p for p in polygonize(unary_union(col_geom)) if p.is_valid and p.area > 0.04]
                        if col_polys:
                            hab_principal = hab_principal.difference(unary_union(col_polys))
                    
                    hab_limpia = hab_principal.simplify(0.05, preserve_topology=True)
                    area_habitable = round(hab_limpia.area, 2)

                    # ==========================================================
                    # EXTRACCIÓN GEOMÉTRICA EXACTA DE VIGAS (S-VIGAS PERALTADAS)
                    # ==========================================================
                    vigas_poligonos_export = []
                    if lineas_vigas_crudo:
                        vigas_geom = [
                            LineString([((m['x1']-min_x)/factor, (m['y1']-min_y)/factor), 
                                        ((m['x2']-min_x)/factor, (m['y2']-min_y)/factor)]) 
                            for m in lineas_vigas_crudo
                        ]
                        red_vigas = snap(unary_union(vigas_geom), unary_union(vigas_geom), tolerance=0.15)
                        # Polygonize encuentra la franja de 30cm formada por las dos líneas paralelas de la viga
                        polys_vigas = [p for p in polygonize(red_vigas) if p.is_valid and p.area > 0.05]
                        
                        # Si son líneas de eje único, se expanden a 30cm
                        if not polys_vigas:
                            polys_vigas = [red_vigas.buffer(0.15, cap_style=2, join_style=2)]

                        # Recortar las vigas para que solo existan dentro de la huella del local
                        for pv in polys_vigas:
                            corte_viga = pv.intersection(hab_limpia)
                            if not corte_viga.is_empty:
                                geoms_v = corte_viga.geoms if hasattr(corte_viga, 'geoms') else [corte_viga]
                                for gv in geoms_v:
                                    if isinstance(gv, Polygon) and gv.area > 0.08:
                                        gv_simp = gv.simplify(0.02, preserve_topology=True)
                                        vigas_poligonos_export.append([{"x": round(x,2), "y": round(y,2)} for x, y in gv_simp.exterior.coords])

                    # Textos y vanos
                    hab_tolerancia = hab_limpia.buffer(0.40)
                    textos_dentro = []
                    textos_fuera = []
                    for t in textos_crudos_dxf:
                        tx_norm = (t["x"] - min_x) / factor
                        ty_norm = (t["y"] - min_y) / factor
                        punto = Point(tx_norm, ty_norm)
                        if hab_tolerancia.contains(punto):
                            textos_dentro.append(t["texto"])
                        else:
                            textos_fuera.append(t["texto"])

                    vanos_detectados = []
                    poligono_exterior = hab_limpia.exterior if hasattr(hab_limpia, 'exterior') else (hab_limpia.geoms[0].exterior if hasattr(hab_limpia, 'geoms') else None)
                    if poligono_exterior:
                        coords_per = list(poligono_exterior.coords)
                        for i in range(len(coords_per) - 1):
                            p1, p2 = coords_per[i], coords_per[i+1]
                            dist = math.hypot(p2[0]-p1[0], p2[1]-p1[1])
                            if 0.70 <= dist <= 2.80:
                                segmento = LineString([p1, p2])
                                if segmento.intersection(union_muros).length < (dist * 0.20):
                                    pm = Point((p1[0]+p2[0])/2.0, (p1[1]+p2[1])/2.0)
                                    nx, ny = -(p2[1]-p1[1])/dist, (p2[0]-p1[0])/dist
                                    test_pt = Point(pm.x + nx*0.2, pm.y + ny*0.2)
                                    if hab_limpia.contains(test_pt):
                                        nx, ny = -nx, -ny
                                    rayo = LineString([(pm.x, pm.y), (pm.x + nx*1.20, pm.y + ny*1.20)])
                                    if rayo.intersection(union_muros).length < 0.1:
                                        rot = round(math.degrees(math.atan2(p2[1]-p1[1], p2[0]-p1[0])), 1)
                                        tipo_vano = "puerta" if 0.75 <= dist <= 1.30 else "ventana"
                                        if not any(math.hypot(pm.x - v['cx'], pm.y - v['cy']) < 0.5 for v in vanos_detectados):
                                            vanos_detectados.append({
                                                "tipo": tipo_vano, "cx": pm.x, "cy": pm.y,
                                                "p1": {"x": round(p1[0],2), "y": round(p1[1],2)},
                                                "p2": {"x": round(p2[0],2), "y": round(p2[1],2)},
                                                "ancho": round(dist, 2), "rot": rot, "espesor": 0.15
                                            })

                # Inferencia con Gemini
                with st.spinner("🤖 Analizando doble altura (6.80m vs 4.50m) y vigas con Gemini..."):
                    perfil_vertical = deducir_alturas_multicota(textos_dentro, textos_fuera, alturas_medidas_norm)

                # Aforo Tetris
                with st.spinner("🪑 Maximizando Aforo Comercial..."):
                    aforo_legal_max = math.floor(area_habitable / factor_area)
                    mesas_4p, mesas_2p = [], []
                    area_interior = hab_limpia.buffer(-0.80) 
                    burbujas_geom = []
                    for v in vanos_detectados:
                        if v['tipo'] == 'puerta':
                            burbujas_geom.append(Point(v['cx'], v['cy']).buffer(1.20))
                    red_prohibida = unary_union(burbujas_geom)

                    if area_habitable >= 6.0 and not area_interior.is_empty:
                        bx1, by1, bx2, by2 = hab_limpia.bounds
                        paso_4p = 1.90 
                        x_c = bx1 + 0.4
                        while x_c <= bx2:
                            y_c = by1 + 0.4
                            while y_c <= by2:
                                pt = Point(x_c, y_c)
                                if area_interior.contains(pt) and not red_prohibida.contains(pt):
                                    if all(math.hypot(x_c - m['x'], y_c - m['y']) >= 1.80 for m in mesas_4p):
                                        mesas_4p.append({"id": "mesa_4p.skp", "x": round(x_c,2), "y": round(y_c,2), "z": 0.0, "rot": 0, "asientos": 4})
                                y_c += paso_4p
                            x_c += paso_4p

                        paso_2p = 1.30
                        rx = bx1 + 0.4
                        while rx <= bx2:
                            ry = by1 + 0.4
                            while ry <= by2:
                                pt_res = Point(rx, ry)
                                if area_interior.contains(pt_res) and not red_prohibida.contains(pt_res):
                                    libre_4p = all(math.hypot(rx - m['x'], ry - m['y']) >= 1.60 for m in mesas_4p)
                                    libre_2p = all(math.hypot(rx - m['x'], ry - m['y']) >= 1.30 for m in mesas_2p)
                                    if libre_4p and libre_2p:
                                        mesas_2p.append({"id": "mesa_2p.skp", "x": round(rx,2), "y": round(ry,2), "z": 0.0, "rot": 90, "asientos": 2})
                                ry += paso_2p
                            rx += paso_2p

                    asientos_totales = (len(mesas_4p)*4) + (len(mesas_2p)*2)
                    
                    muros_export = []
                    for p in muros_solidos:
                        p_simp = p.simplify(0.02, preserve_topology=True)
                        muros_export.append([{"x": round(x,2), "y": round(y,2)} for x,y in p_simp.exterior.coords])

                    coords_contorno = []
                    if hasattr(hab_limpia, 'exterior'):
                        coords_contorno = [{"x": round(x,2), "y": round(y,2)} for x,y in hab_limpia.exterior.coords]
                    elif hasattr(hab_limpia, 'geoms'):
                        coords_contorno = [{"x": round(x,2), "y": round(y,2)} for x,y in hab_limpia.geoms[0].exterior.coords]

                    # CONTRATO JSON ENRIQUECIDO CON VIGAS REALES
                    datos_proyecto = {
                        "espacio": perfil_vertical["nombre_espacio"],
                        "altura_muros": perfil_vertical["altura_losa"],         # 6.80m a la losa
                        "altura_libre_acceso": perfil_vertical["altura_libre"],  # 4.50m al marco
                        "nivel_piso": perfil_vertical["nivel_piso_dominante"],   # 5.95m
                        "perfil_vertical": {
                            "altura_losa": perfil_vertical["altura_losa"],
                            "altura_libre": perfil_vertical["altura_libre"],
                            "pisos": perfil_vertical["pisos"],
                            "elementos_techo": perfil_vertical["elementos_techo"],
                            "observacion": perfil_vertical["observacion"]
                        },
                        "muros": muros_export,
                        "vigas_reales": vigas_poligonos_export,                 # Coordenadas exactas CAD
                        "contorno_piso": coords_contorno,
                        "vanos": vanos_detectados,
                        "muebles": mesas_4p + mesas_2p,
                        "reporte": {
                            "area_m2": area_habitable, 
                            "aforo_legal": aforo_legal_max, 
                            "asientos": asientos_totales
                        }
                    }
                    
                    st.session_state['datos_proyecto'] = json.dumps(datos_proyecto, indent=4)
                    st.session_state['metrics'] = {
                        "area": area_habitable, 
                        "legal": aforo_legal_max, 
                        "asientos": asientos_totales,
                        "espacio": perfil_vertical["nombre_espacio"],
                        "altura_losa": perfil_vertical["altura_losa"],
                        "altura_libre": perfil_vertical["altura_libre"],
                        "npt": perfil_vertical["nivel_piso_dominante"],
                        "pisos": perfil_vertical["pisos"],
                        "techos": perfil_vertical["elementos_techo"],
                        "vigas_detectadas": len(vigas_poligonos_export),
                        "obs": perfil_vertical["observacion"]
                    }

            except Exception as e:
                st.error(f"❌ Error al procesar: {e}")

# ==============================================================================
# 3. INTERFAZ Y DASHBOARD
# ==============================================================================
if st.session_state['datos_proyecto']:
    m = st.session_state['metrics']
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("🏷️ Espacio", f"{m['espacio']}")
    col2.metric("📐 Altura a Losa (Real)", f"{m['altura_losa']} m")
    col3.metric("🚪 Altura Libre Acceso", f"{m['altura_libre']} m")
    col4.metric("🪑 Asientos Diseñados", f"{m['asientos']} Pax")

    st.success(f"Cota N.P.T.: {m['npt']} m | Área Útil: {m['area']} m² | Vigas CAD Exactas: {m['vigas_detectadas']}")

    with st.expander("📊 Desglose de Alturas, Vigas y Marcos Detectados"):
        st.markdown(f"**Observación de Ingeniería:** {m['obs']}")
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            st.markdown("### 📍 Niveles de Piso (N.P.T.)")
            for p in m['pisos']:
                st.write(f"• **{p.get('etiqueta')}**: `{p.get('cota_metros')} m`")
        with col_m2:
            st.markdown("### 🏗️ Estructura Superior")
            for t in m['techos']:
                desc = f" (Descuelgue: -{t.get('descuelgue_metros')}m)" if t.get('descuelgue_metros', 0) > 0 else ""
                st.write(f"• **{t.get('descripcion')}**: `{t.get('cota_inferior_metros')} m`{desc}")

    st.download_button("📦 Descargar JSON de Patty", data=st.session_state['datos_proyecto'], file_name="proyecto.json")
