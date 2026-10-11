import streamlit as st
import ezdxf
import json
import tempfile
import os
import math
from shapely.geometry import LineString, Point, Polygon, MultiPolygon
from shapely.ops import unary_union, polygonize, snap

st.set_page_config(page_title="Patty | ConTech Engine", page_icon="🏗️", layout="centered")
st.title("🏗️ Patty - Generador Arquitectónico Paramétrico")
st.markdown("---")

if 'datos_proyecto' not in st.session_state:
    st.session_state['datos_proyecto'] = None
    st.session_state['metrics'] = None

factor_area = 1.50  
pasillo_min = 1.20  

archivo_dxf = st.file_uploader("📥 Arrastra aquí el plano CAD (.DXF)", type=['dxf'])

if archivo_dxf is not None:
    if st.button("🚀 Generar 3D Automáticamente"):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp_file:
            tmp_file.write(archivo_dxf.getvalue())
            ruta_temporal = tmp_file.name

        try:
            with st.spinner("🧠 Analizando geometría y deduciendo espesores..."):
                doc = ezdxf.readfile(ruta_temporal)
                msp = doc.modelspace()
                muros_crudos = []
                for e in msp.query('LINE LWPOLYLINE'):
                    if e.dxftype() == 'LINE':
                        p1, p2 = (e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)
                        if p1 != p2: muros_crudos.append({"x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1]})
                    elif e.dxftype() == 'LWPOLYLINE':
                        pts = [(float(p[0]), float(p[1])) for p in e.get_points('xy')]
                        for i in range(len(pts) - 1):
                            if pts[i] != pts[i+1]: muros_crudos.append({"x1": pts[i][0], "y1": pts[i][1], "x2": pts[i+1][0], "y2": pts[i+1][1]})
                        if getattr(e, 'is_closed', False) and pts[-1] != pts[0]:
                            muros_crudos.append({"x1": pts[-1][0], "y1": pts[-1][1], "x2": pts[0][0], "y2": pts[0][1]})

                todos_x = [m['x1'] for m in muros_crudos] + [m['x2'] for m in muros_crudos]
                todos_y = [m['y1'] for m in muros_crudos] + [m['y2'] for m in muros_crudos]
                min_x, min_y = min(todos_x), min(todos_y)
                dim_mayor = max(max(todos_x) - min_x, max(todos_y) - min_y)
                factor = 1.0 if dim_mayor < 200 else (100.0 if dim_mayor < 25000 else 1000.0)

                lineas_limpias = [LineString([((m['x1']-min_x)/factor, (m['y1']-min_y)/factor), ((m['x2']-min_x)/factor, (m['y2']-min_y)/factor)]) for m in muros_crudos if m['x1']!=m['x2'] or m['y1']!=m['y2']]

                red_unida = unary_union(snap(unary_union(lineas_limpias), unary_union(lineas_limpias), tolerance=0.10))
                muros_solidos = [p for p in polygonize(red_unida) if p.is_valid and p.buffer(-0.10).is_empty]
                if not muros_solidos:
                    muros_solidos = list(red_unida.buffer(0.10).geoms) if isinstance(red_unida.buffer(0.10), MultiPolygon) else [red_unida.buffer(0.10)]

                union_muros = unary_union(muros_solidos)
                envoltura = union_muros.convex_hull
                vacios = envoltura.difference(union_muros)
                
                cands = [g for g in (vacios.geoms if hasattr(vacios, 'geoms') else [vacios]) if isinstance(g, Polygon) and g.area > 2.0]
                hab_principal = max(cands, key=lambda h: h.area) if cands else envoltura
                area_habitable = round(hab_principal.area, 2)

                # ESCÁNER DE VANOS
                vanos_detectados = []
                coords_per = list(hab_principal.exterior.coords)
                for i in range(len(coords_per) - 1):
                    p1, p2 = coords_per[i], coords_per[i+1]
                    dist = math.hypot(p2[0]-p1[0], p2[1]-p1[1])
                    if 0.70 <= dist <= 2.80:
                        pm = Point((p1[0]+p2[0])/2.0, (p1[1]+p2[1])/2.0)
                        if pm.distance(union_muros) > 0.05:
                            rot = round(math.degrees(math.atan2(p2[1]-p1[1], p2[0]-p1[0])), 1)
                            if not any(math.hypot(pm.x - v['cx'], pm.y - v['cy']) < 0.5 for v in vanos_detectados):
                                
                                espesor_real = 0.15
                                for poly in muros_solidos:
                                    if poly.distance(pm) < 0.2:
                                        aprox = poly.area / (poly.length / 2.0)
                                        estandares = [0.10, 0.15, 0.20, 0.25, 0.30]
                                        espesor_real = min(estandares, key=lambda x: abs(x - aprox))
                                        break

                                vanos_detectados.append({
                                    "tipo": "puerta", "cx": pm.x, "cy": pm.y,
                                    "p1": {"x": round(p1[0],2), "y": round(p1[1],2)},
                                    "p2": {"x": round(p2[0],2), "y": round(p2[1],2)},
                                    "ancho": round(dist, 2), "rot": rot, "espesor": espesor_real
                                })

            with st.spinner("🪑 Distribuyendo Mobiliario sin Colisiones..."):
                aforo_legal_max = math.floor(area_habitable / factor_area)
                mesas_4p, mesas_2p = [], []
                
                area_interior = hab_principal.buffer(-1.00) 
                centro_local = hab_principal.centroid
                
                pasillos_geom = [LineString([(v['cx'], v['cy']), (centro_local.x, centro_local.y)]).buffer(pasillo_min / 2.0) for v in vanos_detectados]
                red_prohibida = unary_union(pasillos_geom)

                if not area_interior.is_empty and area_habitable >= 6.0:
                    bx1, by1, bx2, by2 = area_interior.bounds
                    
                    paso_4p = 2.50 
                    x_c = bx1 + 0.6
                    while x_c <= bx2:
                        y_c = by1 + 0.6
                        while y_c <= by2:
                            pt = Point(x_c, y_c)
                            if area_interior.contains(pt) and not red_prohibida.contains(pt):
                                if all(math.hypot(x_c - m['x'], y_c - m['y']) >= 2.2 for m in mesas_4p):
                                    mesas_4p.append({"id": "mesa_4p.skp", "x": round(x_c,2), "y": round(y_c,2), "z": 0.0, "rot": 0, "asientos": 4})
                            y_c += paso_4p
                        x_c += paso_4p

                    zonas_ocupadas = [Point(m['x'], m['y']).buffer(1.20) for m in mesas_4p] + pasillos_geom
                    area_residual = area_interior.difference(unary_union(zonas_ocupadas))
                    
                    if not area_residual.is_empty:
                        paso_2p = 1.80
                        rx = bx1 + 0.5
                        while rx <= bx2:
                            ry = by1 + 0.5
                            while ry <= by2:
                                pt_res = Point(rx, ry)
                                if area_residual.contains(pt_res):
                                    # RADAR AMPLIADO: 1.60m mínimo de separación
                                    if all(math.hypot(rx - m['x'], ry - m['y']) >= 1.6 for m in mesas_2p):
                                        mesas_2p.append({"id": "mesa_2p.skp", "x": round(rx,2), "y": round(ry,2), "z": 0.0, "rot": 0, "asientos": 2})
                                ry += paso_2p
                            rx += paso_2p

                asientos_totales = (len(mesas_4p)*4) + (len(mesas_2p)*2)
                
                datos_proyecto = {
                    "altura_muros": 2.80,
                    "muros": [[{"x": round(x,2), "y": round(y,2)} for x,y in p.exterior.coords] for p in muros_solidos],
                    "contorno_piso": [{"x": round(x,2), "y": round(y,2)} for x,y in hab_principal.exterior.coords],
                    "vanos": vanos_detectados,
                    "muebles": mesas_4p + mesas_2p,
                    "reporte": {"area_m2": area_habitable, "aforo_legal": aforo_legal_max, "asientos": asientos_totales}
                }
                
                st.session_state['datos_proyecto'] = json.dumps(datos_proyecto, indent=4)
                st.session_state['metrics'] = {
                    "area": area_habitable, "legal": aforo_legal_max, "asientos": asientos_totales
                }

        except Exception as e:
            st.error(f"❌ Error: {e}")
        finally:
            if os.path.exists(ruta_temporal): os.remove(ruta_temporal)

if st.session_state['datos_proyecto']:
    m = st.session_state['metrics']
    col1, col2, col3 = st.columns(3)
    col1.metric("📐 Área Útil", f"{m['area']} m²")
    col2.metric("🏛️ Aforo Legal Máx.", f"{m['legal']} Pax")
    col3.metric("🪑 Asientos Diseñados", f"{m['asientos']} Pax")

    st.download_button("📦 Descargar Modelo.json", data=st.session_state['datos_proyecto'], file_name="proyecto.json")
