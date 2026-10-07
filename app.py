import streamlit as st
import ezdxf
import json
import tempfile
import os
import math
from shapely.geometry import LineString, Point, Polygon, MultiPolygon, GeometryCollection
from shapely.ops import unary_union, polygonize, snap

# Configuración de la pestaña
st.set_page_config(page_title="Patty | Generador Arquitectónico", page_icon="🏗️", layout="centered")

st.title("🏗️ Patty - Motor de Automatización 2D a 3D")
st.subheader("Transforma tus planos de CAD a modelos 3D y presupuestos en segundos.")
st.markdown("---")

archivo_dxf = st.file_uploader("📥 Arrastra aquí el plano de tu cliente (.DXF)", type=['dxf'])

if archivo_dxf is not None:
    st.success(f"✅ Archivo '{archivo_dxf.name}' cargado con éxito.")
    
    if st.button("🚀 Iniciar Generación Automática"):
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp_file:
            tmp_file.write(archivo_dxf.getvalue())
            ruta_temporal = tmp_file.name

        try:
            with st.spinner("🧠 1/3 Leyendo geometría y normalizando al origen..."):
                doc = ezdxf.readfile(ruta_temporal)
                msp = doc.modelspace()
                
                muros_crudos = []
                
                # 1. Extracción de líneas y polilíneas
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
                    st.error("❌ No se encontraron líneas válidas en el plano.")
                    st.stop()

                # 2. Detección de escala basada en dimensiones relativas (Bounding Box)
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

                # Traslación a (0, 0) en metros reales
                lineas_limpias = []
                for m in muros_crudos:
                    p1 = ((m['x1'] - min_x) / factor, (m['y1'] - min_y) / factor)
                    p2 = ((m['x2'] - min_x) / factor, (m['y2'] - min_y) / factor)
                    if p1 != p2:
                        lineas_limpias.append(LineString([p1, p2]))

            with st.spinner("🧹 2/3 Sellando vanos de puertas y extrayendo habitación..."):
                red_lineas = unary_union(lineas_limpias)
                red_snapped = snap(red_lineas, red_lineas, tolerance=0.10)
                red_unida = unary_union(red_snapped)

                poligonos_crudos = list(polygonize(red_unida))
                muros_solidos = []
                
                for poly in poligonos_crudos:
                    if not poly.is_valid or poly.area < 0.05:
                        continue
                    # Si al erosionar 15 cm desaparece, es un muro sólido de concreto
                    if poly.buffer(-0.15).is_empty:
                        muros_solidos.append(poly)

                # Si no encontramos muros dobles cerrados, usamos las líneas con buffer
                if not muros_solidos:
                    muro_buffer = red_unida.buffer(0.10)
                    if isinstance(muro_buffer, MultiPolygon):
                        muros_solidos = list(muro_buffer.geoms)
                    else:
                        muros_solidos = [muro_buffer]

                union_muros = unary_union(muros_solidos)
                envoltura_total = union_muros.convex_hull

                # EXTRACCIÓN DEL RECINTO INTERIOR (Sustracción Morfológica)
                # El espacio habitable es el interior de la envoltura menos la masa de concreto de los muros
                espacio_vacio = envoltura_total.difference(union_muros)
                
                candidatos_habitacion = []
                if isinstance(espacio_vacio, Polygon):
                    candidatos_habitacion = [espacio_vacio]
                elif isinstance(espacio_vacio, (MultiPolygon, GeometryCollection)):
                    for g in espacio_vacio.geoms:
                        if isinstance(g, Polygon) and g.area > 2.0:
                            candidatos_habitacion.append(g)

                if candidatos_habitacion:
                    # La habitación principal es el espacio habitable mayor
                    habitacion_principal = max(candidatos_habitacion, key=lambda h: h.area)
                else:
                    habitacion_principal = envoltura_total

                area_habitable = round(habitacion_principal.area, 2)

                # 3. DETECCIÓN AUTOMÁTICA DE PUERTAS EN LOS VANOS REALES
                # Buscamos extremos de muros que tengan separación entre 0.70m y 1.25m
                extremos_muros = []
                for poly in muros_solidos:
                    coords = list(poly.exterior.coords)
                    for pt in coords:
                        extremos_muros.append(pt)

                vano_detectado = None
                for i in range(len(extremos_muros)):
                    for j in range(i + 1, len(extremos_muros)):
                        p_a = extremos_muros[i]
                        p_b = extremos_muros[j]
                        dist_vano = math.hypot(p_b[0] - p_a[0], p_b[1] - p_a[1])
                        # Ancho estándar de vano de puerta
                        if 0.75 <= dist_vano <= 1.25:
                            # Verificar que el vano colinde con la habitación
                            punto_medio = Point((p_a[0] + p_b[0])/2.0, (p_a[1] + p_b[1])/2.0)
                            if habitacion_principal.distance(punto_medio) < 0.5:
                                vano_detectado = (p_a, p_b)
                                break
                    if vano_detectado:
                        break

                if vano_detectado:
                    p1_v, p2_v = vano_detectado
                    x_puerta = (p1_v[0] + p2_v[0]) / 2.0
                    y_puerta = (p1_v[1] + p2_v[1]) / 2.0
                    rot_puerta = round(math.degrees(math.atan2(p2_v[1] - p1_v[1], p2_v[0] - p1_v[0])), 1)
                else:
                    # Fallback al centroide del primer segmento del recinto
                    ext_coords = list(habitacion_principal.exterior.coords)
                    x_puerta = (ext_coords[0][0] + ext_coords[1][0]) / 2.0
                    y_puerta = (ext_coords[0][1] + ext_coords[1][1]) / 2.0
                    rot_puerta = 0.0

                puertas = [{
                    "id": "puerta.skp",
                    "x": round(x_puerta, 2),
                    "y": round(y_puerta, 2),
                    "z": 0.0,
                    "rot": rot_puerta
                }]

            with st.spinner("🪑 3/3 Generando distribución de mobiliario..."):
                # Aforo de mesas en el interior habitable (clearance perimetral de 1 metro)
                mesas = []
                area_mesas = habitacion_principal.buffer(-1.0)
                
                if not area_mesas.is_empty and area_habitable >= 6.0:
                    bx1, by1, bx2, by2 = area_mesas.bounds
                    paso = 2.0  # Espaciado de 2 metros entre ejes de mesas
                    
                    x_c = bx1 + 0.6
                    while x_c <= bx2:
                        y_c = by1 + 0.6
                        while y_c <= by2:
                            pt = Point(x_c, y_c)
                            if area_mesas.contains(pt) and pt.distance(Point(x_puerta, y_puerta)) > 1.8:
                                mesas.append({
                                    "id": "mesa.skp",
                                    "x": round(x_c, 2),
                                    "y": round(y_c, 2),
                                    "z": 0.0,
                                    "rot": 0
                                })
                            y_c += paso
                        x_c += paso

                # ESTRUCTURACIÓN DE MUROS INDEPENDIENTES PARA SKETCHUP
                muros_export = []
                for p in muros_solidos:
                    muros_export.append([{"x": round(x, 2), "y": round(y, 2)} for x, y in p.exterior.coords])

                datos_proyecto = {
                    "altura_muros": 2.80,
                    "muros": muros_export,
                    "contorno_piso": [{"x": round(x, 2), "y": round(y, 2)} for x, y in habitacion_principal.exterior.coords],
                    "puertas": puertas,
                    "muebles": mesas,
                    "reporte": {
                        "area_m2": area_habitable,
                        "aforo_mesas": len(mesas),
                        "muros_detectados": len(muros_solidos)
                    }
                }
                
                json_string = json.dumps(datos_proyecto, indent=4)

            st.balloons()
            st.success("🎉 ¡PROYECTO PROCESADO CON ÉXITO!")
            
            # Métricas del modelo
            col1, col2, col3 = st.columns(3)
            col1.metric("📐 Área Habitable", f"{area_habitable} m²", f"Muros: {len(muros_solidos)}")
            col2.metric("🪑 Aforo Máximo", f"{len(mesas)} Mesas", "Clearance 1.0m")
            col3.metric("🚪 Vano de Acceso", f"{rot_puerta}°", f"({round(x_puerta,2)}, {round(y_puerta,2)})")
            
            st.markdown("---")
            st.markdown("### 📥 Descarga tus archivos puente:")
            
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

st.markdown("---")
st.caption("Patty Engine MVP v1.3 | Sustracción Morfológica y Vanos Reales")
