import streamlit as st
import ezdxf
import json
import tempfile
import os
import math
from shapely.geometry import LineString, Point, Polygon, MultiPolygon, GeometryCollection
from shapely.ops import unary_union, polygonize, snap

# Configuración de la pestaña
st.set_page_config(page_title="Patty | ConTech Engine", page_icon="🏗️", layout="wide")

st.title("🏗️ Patty - Generador Arquitectónico Paramétrico")
st.caption("De planos CAD 2D sucios a modelos 3D y cumplimiento normativo en segundos.")
st.markdown("---")

# BARRA LATERAL: MOTOR DE CUMPLIMIENTO NORMATIVO
st.sidebar.header("🏛️ Parámetros de Cumplimiento")
normativa_sel = st.sidebar.selectbox(
    "Normativa Aplicable",
    ["LatAm - RNE (Norma A.070 / A.130)", "EE.UU. / Int. - IBC / NFPA 101", "España - CTE (DB-SI)"]
)

tipo_local = st.sidebar.selectbox(
    "Giro / Uso del Espacio",
    ["Restaurante Comercial", "Cafetería / Bistro", "Bar / Lounge"]
)

# Factores normativos según jurisdicción y tipo de uso
if "RNE" in normativa_sel:
    factor_area_pax = 1.50 if "Restaurante" in tipo_local else 1.20
    pasillo_min = 1.20
elif "IBC" in normativa_sel:
    factor_area_pax = 1.40 if "Restaurante" in tipo_local else 1.10
    pasillo_min = 1.00
else:  # CTE España
    factor_area_pax = 1.50 if "Restaurante" in tipo_local else 1.25
    pasillo_min = 1.00

st.sidebar.markdown(f"""
**Estándar Regulatorio Activo:**
* Factor de Aforo: `{factor_area_pax} m²/comensal`
* Pasillo Mínimo Evacuación: `{pasillo_min} m`
""")

# ZONA DE CARGA
archivo_dxf = st.file_uploader("📥 Arrastra aquí el plano CAD (.DXF)", type=['dxf'])

if archivo_dxf is not None:
    st.success(f"✅ Archivo '{archivo_dxf.name}' listo para procesamiento.")
    
    if st.button("🚀 Procesar Plano y Generar Espacio 3D"):
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp_file:
            tmp_file.write(archivo_dxf.getvalue())
            ruta_temporal = tmp_file.name

        try:
            with st.spinner("🧠 1/3 Analizando geometría y normalizando al origen..."):
                doc = ezdxf.readfile(ruta_temporal)
                msp = doc.modelspace()
                
                muros_crudos = []
                for e in msp.query('LINE LWPOLYLINE'):
                    if e.dxftype() == 'LINE':
                        p1 = (float(e.dxf.start.x), float(e.dxf.start.y))
                        p2 = (float(e.dxf.end.x), float(e.dxf.end.y))
                        if p1 != p2:
                            muros_crudos.append({"x1": p1[0], "y1": p1[1], "x2": p2[0], "y2": p2[1]})
                    elif e.dxftype() == 'LWPOLYLINE':
                        pts = [(float(p[0]), float(p[1])) for p in e.get_points('xy')]
                        if len(pts) > 1:
                            for i in range(len(pts) - 1):
                                if pts[i] != pts[i+1]:
                                    muros_crudos.append({
                                        "x1": pts[i][0], "y1": pts[i][1],
                                        "x2": pts[i+1][0], "y2": pts[i+1][1]
                                    })
                            if getattr(e, 'is_closed', False) or getattr(e, 'closed', False):
                                if pts[-1] != pts[0]:
                                    muros_crudos.append({
                                        "x1": pts[-1][0], "y1": pts[-1][1],
                                        "x2": pts[0][0], "y2": pts[0][1]
                                    })

                if not muros_crudos:
                    st.error("❌ No se encontraron líneas válidas en el archivo.")
                    st.stop()

                todos_x = [m['x1'] for m in muros_crudos] + [m['x2'] for m in muros_crudos]
                todos_y = [m['y1'] for m in muros_crudos] + [m['y2'] for m in muros_crudos]
                
                min_x, max_x = min(todos_x), max(todos_x)
                min_y, max_y = min(todos_y), max(todos_y)
                dim_mayor = max(max_x - min_x, max_y - min_y)

                if dim_mayor < 200.0:
                    factor = 1.0
                    unidad_origen = "Metros"
                elif dim_mayor < 25000.0:
                    factor = 100.0
                    unidad_origen = "Centímetros"
                else:
                    factor = 1000.0
                    unidad_origen = "Milímetros"

                lineas_limpias = []
                for m in muros_crudos:
                    p1 = ((m['x1'] - min_x) / factor, (m['y1'] - min_y) / factor)
                    p2 = ((m['x2'] - min_x) / factor, (m['y2'] - min_y) / factor)
                    if p1 != p2:
                        lineas_limpias.append(LineString([p1, p2]))

            with st.spinner("🧹 2/3 Procesando muros y detectando vanos..."):
                red_lineas = unary_union(lineas_limpias)
                red_snapped = snap(red_lineas, red_lineas, tolerance=0.10)
                red_unida = unary_union(red_snapped)

                poligonos_crudos = list(polygonize(red_unida))
                muros_solidos = []
                for poly in poligonos_crudos:
                    if not poly.is_valid or poly.area < 0.05:
                        continue
                    if poly.buffer(-0.15).is_empty:
                        muros_solidos.append(poly)

                if not muros_solidos:
                    muro_buffer = red_unida.buffer(0.10)
                    muros_solidos = list(muro_buffer.geoms) if isinstance(muro_buffer, MultiPolygon) else [muro_buffer]

                union_muros = unary_union(muros_solidos)
                envoltura_total = union_muros.convex_hull

                espacio_vacio = envoltura_total.difference(union_muros)
                candidatos = []
                if isinstance(espacio_vacio, Polygon):
                    candidatos = [espacio_vacio]
                elif isinstance(espacio_vacio, (MultiPolygon, GeometryCollection)):
                    for g in espacio_vacio.geoms:
                        if isinstance(g, Polygon) and g.area > 2.0:
                            candidatos.append(g)

                habitacion_principal = max(candidatos, key=lambda h: h.area) if candidatos else envoltura_total
                area_habitable = round(habitacion_principal.area, 2)

                # Detección de Vanos
                extremos = []
                for poly in muros_solidos:
                    for pt in poly.exterior.coords:
                        extremos.append(pt)

                vanos_detectados = []
                for i in range(len(extremos)):
                    for j in range(i + 1, len(extremos)):
                        pa, pb = extremos[i], extremos[j]
                        dist = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
                        if 0.70 <= dist <= 2.00:
                            pm = Point((pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0)
                            if habitacion_principal.distance(pm) < 0.6:
                                if not any(math.hypot(pm.x - v['x'], pm.y - v['y']) < 0.5 for v in vanos_detectados):
                                    dx = pb[0] - pa[0]
                                    dy = pb[1] - pa[1]
                                    rot = round(math.degrees(math.atan2(dy, dx)), 1)
                                    vanos_detectados.append({
                                        "tipo": "puerta" if dist <= 1.25 else "ventana",
                                        "x": round(pm.x, 2),
                                        "y": round(pm.y, 2),
                                        "p1": {"x": round(pa[0], 2), "y": round(pa[1], 2)},
                                        "p2": {"x": round(pb[0], 2), "y": round(pb[1], 2)},
                                        "ancho": round(dist, 2),
                                        "rot": rot
                                    })

            with st.spinner("🪑 3/3 Ejecutando Algoritmo Tetris (Mobiliario Mixto)..."):
                # CÁLCULO DE AFORO LEGAL MÁXIMO
                aforo_legal_max = math.floor(area_habitable / factor_area_pax)

                # ALGORITMO TETRIS: EMPAQUETAMIENTO EN DOS PASADAS
                puntos_vanos = [Point(v['x'], v['y']) for v in vanos_detectados]
                area_interior = habitacion_principal.buffer(-0.80) # 80 cm de margen a muros
                
                mesas_4p = []
                mesas_2p = []

                if not area_interior.is_empty and area_habitable >= 6.0:
                    bx1, by1, bx2, by2 = area_interior.bounds
                    
                    # PASADA 1: Mesas Principales de 4 Personas (Paso 2.0m)
                    paso_4p = 2.0
                    x_c = bx1 + 0.6
                    while x_c <= bx2:
                        y_c = by1 + 0.6
                        while y_c <= by2:
                            pt = Point(x_c, y_c)
                            despeje_accesos = all(pt.distance(pv) > (pasillo_min + 0.6) for pv in puntos_vanos)
                            if area_interior.contains(pt) and despeje_accesos:
                                mesas_4p.append({
                                    "id": "mesa_4p.skp",
                                    "x": round(x_c, 2),
                                    "y": round(y_c, 2),
                                    "z": 0.0,
                                    "rot": 0,
                                    "tipo": "mesa_4p",
                                    "asientos": 4
                                })
                            y_c += paso_4p
                        x_c += paso_4p

                    # PASADA 2 (TETRIS): Absorción de Residuos con Mesas de 2 Personas (Módulo 1.4m)
                    # Excluimos las zonas ya ocupadas por mesas de 4
                    zonas_ocupadas = [Point(m['x'], m['y']).buffer(1.10) for m in mesas_4p]
                    zonas_ocupadas.extend([pv.buffer(pasillo_min + 0.5) for pv in puntos_vanos])
                    area_residual = area_interior.difference(unary_union(zonas_ocupadas))

                    if not area_residual.is_empty:
                        paso_2p = 1.4
                        rx = bx1 + 0.4
                        while rx <= bx2:
                            ry = by1 + 0.4
                            while ry <= by2:
                                pt_res = Point(rx, ry)
                                if area_residual.contains(pt_res):
                                    # Verificar que no colisione con otra mesa de 2 recién colocada
                                    sin_choque = all(pt_res.distance(Point(m['x'], m['y'])) > 1.35 for m in mesas_2p)
                                    if sin_choque:
                                        mesas_2p.append({
                                            "id": "mesa_2p.skp",
                                            "x": round(rx, 2),
                                            "y": round(ry, 2),
                                            "z": 0.0,
                                            "rot": 0,
                                            "tipo": "mesa_2p",
                                            "asientos": 2
                                        })
                                ry += paso_2p
                            rx += paso_2p

                todas_las_mesas = mesas_4p + mesas_2p
                asientos_totales = (len(mesas_4p) * 4) + (len(mesas_2p) * 2)
                cumple_norma = asientos_totales <= aforo_legal_max

                muros_export = []
                for p in muros_solidos:
                    muros_export.append([{"x": round(x, 2), "y": round(y, 2)} for x, y in p.exterior.coords])

                datos_proyecto = {
                    "altura_muros": 2.80,
                    "muros": muros_export,
                    "contorno_piso": [{"x": round(x, 2), "y": round(y, 2)} for x, y in habitacion_principal.exterior.coords],
                    "vanos": vanos_detectados,
                    "puertas": [v for v in vanos_detectados if v['tipo'] == 'puerta'],
                    "muebles": todas_las_mesas,
                    "reporte": {
                        "area_m2": area_habitable,
                        "aforo_legal_bomberos": aforo_legal_max,
                        "asientos_comerciales": asientos_totales,
                        "mesas_4p": len(mesas_4p),
                        "mesas_2p": len(mesas_2p),
                        "normativa_aplicada": normativa_sel,
                        "cumple_normativa": cumple_norma
                    }
                }
                
                json_string = json.dumps(datos_proyecto, indent=4)

            st.balloons()
            st.success("🎉 ¡PROYECTO PROCESADO Y OPTIMIZADO!")
            
            # PANEL DE MÉTRICAS EJECUTIVAS
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("📐 Área Útil", f"{area_habitable} m²")
            col2.metric("🏛️ Aforo Legal Máx.", f"{aforo_legal_max} Pax", "Límite Bomberos")
            col3.metric("🪑 Asientos Diseñados", f"{asientos_totales} Pax", f"{len(mesas_4p)} de 4p + {len(mesas_2p)} de 2p")
            
            if cumple_norma:
                col4.metric("⚖️ Cumplimiento", "APROBADO", "100% Legal", delta_color="normal")
            else:
                col4.metric("⚖️ Cumplimiento", "EXCEDE LÍMITE", "Riesgo de Licencia", delta_color="inverse")

            st.markdown("---")
            st.download_button(
                label="📦 Descargar proyecto.json (Para SketchUp)", 
                data=json_string, 
                file_name="proyecto.json", 
                mime="application/json"
            )

        except Exception as e:
            st.error(f"❌ Error al procesar el plano: {e}")
        
        finally:
            if os.path.exists(ruta_temporal):
                os.remove(ruta_temporal)

st.caption("Patty Engine MVP v2.0 | Algoritmo Tetris y Motor de Cumplimiento Normativo")
