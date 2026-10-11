import streamlit as st
import ezdxf
import json
import tempfile
import os
import math
from shapely.geometry import LineString, Point, Polygon, MultiPolygon, GeometryCollection
from shapely.ops import unary_union, polygonize, snap

st.set_page_config(page_title="Patty | ConTech Engine", page_icon="🏗️", layout="wide")
st.title("🏗️ Patty - Generador Arquitectónico Paramétrico")
st.markdown("---")

st.sidebar.header("🏛️ Parámetros de Cumplimiento")
normativa_sel = st.sidebar.selectbox("Normativa Aplicable", ["LatAm - RNE", "EE.UU. - IBC", "España - CTE"])
tipo_local = st.sidebar.selectbox("Giro / Uso", ["Restaurante Comercial", "Cafetería / Bistro"])

factor_area = 1.50 if "Restaurante" in tipo_local else 1.20
pasillo_min = 1.20 if "RNE" in normativa_sel else 1.00

archivo_dxf = st.file_uploader("📥 Arrastra aquí el plano CAD (.DXF)", type=['dxf'])

if archivo_dxf is not None:
    if st.button("🚀 Procesar Plano y Generar Espacio 3D"):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp_file:
            tmp_file.write(archivo_dxf.getvalue())
            ruta_temporal = tmp_file.name

        try:
            with st.spinner("🧠 1/3 Analizando geometría..."):
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

            with st.spinner("🧹 2/3 Extrayendo muros y calculando espesores..."):
                red_unida = unary_union(snap(unary_union(lineas_limpias), unary_union(lineas_limpias), tolerance=0.10))
                
                poligonos_cerrados = list(polygonize(red_unida))
                muros_solidos = [p for p in poligonos_cerrados if p.is_valid and p.buffer(-0.10).is_empty]
                
                if not muros_solidos:
                    muro_buffer = red_unida.buffer(0.10)
                    muros_solidos = list(muro_buffer.geoms) if isinstance(muro_buffer, MultiPolygon) else [muro_buffer]

                union_muros = unary_union(muros_solidos)
                envoltura = union_muros.convex_hull
                vacios = envoltura.difference(union_muros)
                
                cands = [g for g in (vacios.geoms if hasattr(vacios, 'geoms') else [vacios]) if isinstance(g, Polygon) and g.area > 2.0]
                hab_principal = max(cands, key=lambda h: h.area) if cands else envoltura
                area_habitable = round(hab_principal.area, 2)

                # ESCÁNER CON MEDIDOR DE GROSOR DE MURO
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
                                
                                # ECUACIÓN CUADRÁTICA PARA EL ESPESOR DEL MURO
                                muro_cercano = min(muros_solidos, key=lambda m: m.distance(pm))
                                p_mitad = muro_cercano.length / 2.0
                                area_m = muro_cercano.area
                                discriminante = p_mitad**2 - 4 * area_m
                                if discriminante > 0:
                                    espesor_calc = (p_mitad - math.sqrt(discriminante)) / 2.0
                                    estandar = [0.10, 0.12, 0.15, 0.20, 0.25, 0.30]
                                    espesor_real = min(estandar, key=lambda x: abs(x - espesor_calc))
                                else:
                                    espesor_real = 0.15
                                
                                vanos_detectados.append({
                                    "tipo": "puerta" if dist <= 1.30 else "ventana",
                                    "cx": pm.x, "cy": pm.y,
                                    "p1": {"x": round(p1[0],2), "y": round(p1[1],2)},
                                    "p2": {"x": round(p2[0],2), "y": round(p2[1],2)},
                                    "ancho": round(dist, 2), "rot": rot,
                                    "espesor": espesor_real # <--- AQUÍ ENVÍA EL GROSOR A SKETCHUP
                                })

            with st.spinner("🪑 3/3 Ejecutando Algoritmo de Circulación y Aforo..."):
                aforo_legal_max = math.floor(area_habitable / factor_area)
                mesas_4p, mesas_2p = [], []
                
                area_interior = hab_principal.buffer(-1.00) 
                centro_local = hab_principal.centroid
                
                pasillos_geom = [LineString([(v['cx'], v['cy']), (centro_local.x, centro_local.y)]).buffer(pasillo_min / 2.0) for v in vanos_detectados]
                red_prohibida = unary_union(pasillos_geom)

                if not area_interior.is_empty and area_habitable >= 6.0:
                    bx1, by1, bx2, by2 = area_interior.bounds
                    
                    paso_4p = 2.40 
                    x_c = bx1 + 0.6
                    while x_c <= bx2:
                        y_c = by1 + 0.6
                        while y_c <= by2:
                            pt = Point(x_c, y_c)
                            if area_interior.contains(pt) and not red_prohibida.contains(pt):
                                if all(pt.distance(Point(m['x'], m['y'])) >= 2.0 for m in mesas_4p):
                                    mesas_4p.append({"id": "mesa_4p.skp", "x": round(x_c,2), "y": round(y_c,2), "z": 0.0, "rot": 0, "asientos": 4})
                            y_c += paso_4p
                        x_c += paso_4p

                    zonas_ocupadas = [Point(m['x'], m['y']).buffer(1.20) for m in mesas_4p] + pasillos_geom
                    area_residual = area_interior.difference(unary_union(zonas_ocupadas))
                    
                    if not area_residual.is_empty:
                        paso_2p = 1.40
                        rx = bx1 + 0.5
                        while rx <= bx2:
                            ry = by1 + 0.5
                            while ry <= by2:
                                pt_res = Point(rx, ry)
                                if area_residual.contains(pt_res):
                                    if all(pt_res.distance(Point(m['x'], m['y'])) > 1.2 for m in mesas_2p):
                                        mesas_2p.append({"id": "mesa_2p.skp", "x": round(rx,2), "y": round(ry,2), "z": 0.0, "rot": 0, "asientos": 2})
                                ry += paso_2p
                            rx += paso_2p

                asientos_totales = (len(mesas_4p)*4) + (len(mesas_2p)*2)
                cumple_norma = asientos_totales <= aforo_legal_max
                
                datos_proyecto = {
                    "altura_muros": 2.80,
                    "muros": [[{"x": round(x,2), "y": round(y,2)} for x,y in p.exterior.coords] for p in muros_solidos],
                    "contorno_piso": [{"x": round(x,2), "y": round(y,2)} for x,y in hab_principal.exterior.coords],
                    "vanos": vanos_detectados,
                    "muebles": mesas_4p + mesas_2p,
                    "reporte": {"area_m2": area_habitable, "aforo_legal": aforo_legal_max, "asientos": asientos_totales}
                }
                
                st.download_button("📦 Descargar JSON", data=json.dumps(datos_proyecto, indent=4), file_name="proyecto.json")

        except Exception as e:
            st.error(f"❌ Error: {e}")
        finally:
            if os.path.exists(ruta_temporal): os.remove(ruta_temporal)
