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
# 1. ESQUEMA ESTRICTO GEMINI FLASH (100% PROTOBUF SAFE)
# ==============================================================================
class RecintoInfo(BaseModel):
    nombre_recinto: str = Field(
        description="Nombre del espacio o locatario detectado (ej. MC DONALD'S, LOCAL COMERCIAL, RESTAURANTE, SALA)"
    )
    altura_muro: float = Field(
        description="Altura libre vertical detectada en metros entre piso y losa/viga (ej. 4.50, 2.80)"
    )
    nivel_piso: float = Field(
        description="Cota de nivel de piso terminado NPT en metros (ej. 5.95, 6.00, 0.00)"
    )

class InferenciaCotas(BaseModel):
    recinto: RecintoInfo
    altura_general: float = Field(
        description="Altura global o cota estándar detectada en metros"
    )
    observacion: str = Field(
        description="Detalle de las cotas encontradas en el plano o corte"
    )

def deducir_alturas_con_gemini(textos_interior: list, textos_globales: list) -> tuple:
    api_key = os.environ.get("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", None)
    
    if not api_key:
        st.warning("⚠️ No se encontró GEMINI_API_KEY en Streamlit Secrets.")
        return 2.80, 0.00, "Sin API Key (Default)"

    if not textos_interior and not textos_globales:
        return 2.80, 0.00, "Sin textos en CAD (Default)"

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-3.8-flash")

        prompt = f"""
        Eres un motor ConTech experto en análisis arquitectónico, planos y cortes de construcción.
        Analiza las anotaciones, cotas y directrices extraídas de este archivo CAD:

        - ANOTACIONES ASOCIADAS AL RECINTO:
        {json.dumps(textos_interior, ensure_ascii=False)}

        - ANOTACIONES Y COTAS GENERALES DEL DIBUJO/CORTE:
        {json.dumps(textos_globales, ensure_ascii=False)}

        INSTRUCCIONES CLAVE:
        1. Identifica el nombre del recinto o marca/locatario (ej. 'MC DONALD\'S', 'LOCAL COMERCIAL'). Si no hay, pon 'Espacio Comercial'.
        2. Deduce la altura libre vertical entre piso y losa/viga (ej. cotas dimensionales como '4.50', '4.5', 'COTA 4.50', H=4.50). Conviértela a float en metros. Si no hay, devuelve 2.80.
        3. Identifica niveles de piso terminado (ej. 'NPT +5.95', 'NPT +6.00'). Si no hay cota NPT, devuelve 0.00.
        4. Si encuentras cotas numéricas puras como 'COTA 4.5' o '4.50', evalúa si corresponden a la altura del espacio.
        """

        respuesta = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                response_schema=InferenciaCotas,
                temperature=0.0
            )
        )

        datos = json.loads(respuesta.text)
        recinto = datos.get("recinto", {})
        altura_general = datos.get("altura_general", 2.80)

        altura_final = recinto.get("altura_muro") or altura_general or 2.80
        nivel_piso_final = recinto.get("nivel_piso", 0.00)
        nombre_final = recinto.get("nombre_recinto") or "Espacio Comercial"

        return round(float(altura_final), 2), round(float(nivel_piso_final), 2), nombre_final

    except Exception as e:
        st.error(f"❌ Error al consultar Gemini API: {e}")
        return 2.80, 0.00, "Error en Gemini (Fallback)"

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
                for e in msp.query('LINE LWPOLYLINE'):
                    capas.add(e.dxf.layer)
                st.session_state['capas_disponibles'] = sorted(list(capas))
                
                del doc
                del msp
                gc.collect() 
            except Exception as e:
                st.error(f"Error al leer el archivo: {e}")

    if st.session_state['capas_disponibles']:
        st.info(f"🔍 Detectamos {len(st.session_state['capas_disponibles'])} capas.")
        capas_seleccionadas = st.multiselect(
            "👉 Selecciona SOLO la capa de los MUROS:",
            st.session_state['capas_disponibles']
        )

        if st.button("🚀 Procesar Arquitectura Limpia"):
            if not capas_seleccionadas:
                st.warning("Debes seleccionar al menos una capa.")
                st.stop()
                
            try:
                with st.spinner("🧠 Filtrando geometría y extrayendo anotaciones..."):
                    doc = ezdxf.readfile(st.session_state['archivo_path'])
                    msp = doc.modelspace()
                    
                    muros_crudos = []
                    for e in msp.query('LINE LWPOLYLINE'):
                        if e.dxf.layer in capas_seleccionadas:
                            if e.dxftype() == 'LINE':
                                p1, p2 = (e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)
                                if p1 != p2: muros_crudos.append({"x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1]})
                            elif e.dxftype() == 'LWPOLYLINE':
                                pts = [(float(p[0]), float(p[1])) for p in e.get_points('xy')]
                                for i in range(len(pts) - 1):
                                    if pts[i] != pts[i+1]: muros_crudos.append({"x1": pts[i][0], "y1": pts[i][1], "x2": pts[i+1][0], "y2": pts[i+1][1]})
                                if getattr(e, 'is_closed', False) and pts[-1] != pts[0]:
                                    muros_crudos.append({"x1": pts[-1][0], "y1": pts[-1][1], "x2": pts[0][0], "y2": pts[0][1]})

                    # EXTRACCIÓN DE TEXTOS, COTAS Y DIRECTRICES
                    textos_crudos_dxf = []
                    for t in msp.query('TEXT MTEXT'):
                        if t.dxf.layer not in capas_seleccionadas:
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

                    for ml in msp.query('MULTILEADER'):
                        try:
                            context = getattr(ml, 'context', None)
                            txt_ml = ""
                            if context and hasattr(context, 'm_text') and context.m_text:
                                txt_ml = getattr(context.m_text, 'raw_text', '') or getattr(context.m_text, 'default_text', '')
                            if not txt_ml:
                                txt_ml = getattr(ml.dxf, 'text', '')
                            if txt_ml and txt_ml.strip():
                                textos_crudos_dxf.append({"texto": txt_ml.strip(), "x": 0, "y": 0})
                        except Exception:
                            pass

                    del doc
                    del msp
                    gc.collect()

                    if not muros_crudos:
                        st.error("❌ No se encontraron líneas en las capas seleccionadas.")
                        st.stop()

                    todos_x = [m['x1'] for m in muros_crudos] + [m['x2'] for m in muros_crudos]
                    todos_y = [m['y1'] for m in muros_crudos] + [m['y2'] for m in muros_crudos]
                    min_x, min_y = min(todos_x), min(todos_y)
                    dim_mayor = max(max(todos_x) - min_x, max(todos_y) - min_y)
                    factor = 1.0 if dim_mayor < 200 else (100.0 if dim_mayor < 25000 else 1000.0)

                    lineas_limpias = [LineString([((m['x1']-min_x)/factor, (m['y1']-min_y)/factor), ((m['x2']-min_x)/factor, (m['y2']-min_y)/factor)]) for m in muros_crudos if m['x1']!=m['x2'] or m['y1']!=m['y2']]

                    # TOLERANCIA DE SELLADO AUMENTADA A 0.25m PARA CERRAR ESQUINAS ABIERTAS
                    red_unida = unary_union(snap(unary_union(lineas_limpias), unary_union(lineas_limpias), tolerance=0.25))
                    
                    muros_solidos = [p for p in polygonize(red_unida) if p.is_valid and p.buffer(-0.08).is_empty and p.area > 0.05]
                    if not muros_solidos:
                        muro_buffer = red_unida.buffer(0.10, cap_style=2, join_style=2)
                        muros_solidos = list(muro_buffer.geoms) if isinstance(muro_buffer, MultiPolygon) else [muro_buffer]

                    union_muros = unary_union(muros_solidos)
                    envoltura = union_muros.convex_hull
                    vacios = envoltura.difference(union_muros)
                    
                    todos_vacios = [g for g in (vacios.geoms if hasattr(vacios, 'geoms') else [vacios]) if isinstance(g, Polygon)]
                    areas_detectadas = [round(g.area, 2) for g in todos_vacios]

                    cands = [g for g in todos_vacios if g.area > 2.0]
                    hab_principal = max(cands, key=lambda h: h.area) if cands else (max(todos_vacios, key=lambda h: h.area) if todos_vacios else envoltura)
                    
                    hab_limpia = hab_principal.simplify(0.05, preserve_topology=True)
                    area_habitable = round(hab_limpia.area, 2)

                    # CLASIFICACIÓN ESPACIAL DE TEXTOS
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

                    # ESCÁNER SEMÁNTICO DE VANOS (RAYCAST)
                    vanos_detectados = []
                    coords_per = list(hab_limpia.exterior.coords)
                    
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
                                    
                                    espesor_real = 0.15
                                    for poly in muros_solidos:
                                        if poly.distance(pm) < 0.4:
                                            aprox = poly.area / (poly.length / 2.0)
                                            estandares = [0.10, 0.15, 0.20, 0.25, 0.30]
                                            espesor_real = min(estandares, key=lambda x: abs(x - aprox))
                                            break
                                    
                                    tipo_vano = "puerta" if 0.75 <= dist <= 1.30 else "ventana"

                                    if not any(math.hypot(pm.x - v['cx'], pm.y - v['cy']) < 0.5 for v in vanos_detectados):
                                        vanos_detectados.append({
                                            "tipo": tipo_vano, "cx": pm.x, "cy": pm.y,
                                            "p1": {"x": round(p1[0],2), "y": round(p1[1],2)},
                                            "p2": {"x": round(p2[0],2), "y": round(p2[1],2)},
                                            "ancho": round(dist, 2), "rot": rot, "espesor": espesor_real
                                        })

                with st.spinner("🤖 Leyendo cotas y deduciendo alturas con Gemini Flash..."):
                    altura_deducida, nivel_piso_deducido, nombre_espacio = deducir_alturas_con_gemini(
                        textos_dentro, textos_fuera
                    )

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

                    datos_proyecto = {
                        "espacio": nombre_espacio,
                        "altura_muros": altura_deducida,
                        "nivel_piso": nivel_piso_deducido,
                        "muros": muros_export,
                        "contorno_piso": [{"x": round(x,2), "y": round(y,2)} for x,y in hab_limpia.exterior.coords],
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
                        "espacio": nombre_espacio,
                        "altura": altura_deducida,
                        "npt": nivel_piso_deducido,
                        "total_textos": len(textos_crudos_dxf),
                        "textos_dentro": textos_dentro,
                        "textos_fuera": textos_fuera,
                        "telemetria": {
                            "num_muros": len(muros_crudos),
                            "dim_mayor": round(dim_mayor, 2),
                            "factor": factor,
                            "areas_vacios": areas_detectadas
                        }
                    }

            except Exception as e:
                st.error(f"❌ Error al procesar: {e}")

# ==============================================================================
# 3. INTERFAZ Y TELEMETRÍA EN PANTALLA
# ==============================================================================
if st.session_state['datos_proyecto']:
    m = st.session_state['metrics']
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("🏷️ Espacio", f"{m['espacio']}")
    col2.metric("📐 Altura Muros (H)", f"{m['altura']} m")
    col3.metric("🏛️ Aforo Legal", f"{m['legal']} Pax")
    col4.metric("🪑 Asientos Diseñados", f"{m['asientos']} Pax")

    st.success(f"Cota N.P.T.: {m['npt']} m | Área Útil: {m['area']} m²")

    # TELEMETRÍA TÉCNICA VISIBLE
    t = m['telemetria']
    st.info(f"📊 **Telemetría Geométrica:** Líneas leídas: {t['num_muros']} | Dimensión mayor: {t['dim_mayor']} (Factor escala: {t['factor']}) | Polígonos cerrados detectados: {t['areas_vacios']} m²")

    with st.expander(f"🔍 Auditoría de Textos y Cotas CAD ({m['total_textos']} elementos extraídos)"):
        st.write("**Anotaciones dentro del recinto:**", m['textos_dentro'] if m['textos_dentro'] else "Ninguno")
        st.write("**Anotaciones generales y cotas del plano:**", m['textos_fuera'][:30] if m['textos_fuera'] else "Ninguno")

    st.download_button("📦 Descargar JSON de Patty", data=st.session_state['datos_proyecto'], file_name="proyecto.json")
