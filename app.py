import streamlit as st
import ezdxf
import json
import tempfile
import os
import math
from shapely.geometry import LineString, MultiLineString, Point, Polygon, MultiPolygon
from shapely.ops import unary_union, polygonize, snap

# Configuración de la pestaña
st.set_page_config(page_title="Patty | Generador Arquitectónico", page_icon="🏗️", layout="centered")

st.title("🏗️ Patty - Motor de Automatización 2D a 3D")
st.subheader("Transforma tus planos de CAD a modelos 3D y presupuestos en segundos.")
st.markdown("---")

# Zona de carga
archivo_dxf = st.file_uploader("📥 Arrastra aquí el plano de tu cliente (.DXF)", type=['dxf'])

if archivo_dxf is not None:
    st.success(f"✅ Archivo '{archivo_dxf.name}' cargado con éxito.")
    
    if st.button("🚀 Iniciar Generación Automática"):
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp_file:
            tmp_file.write(archivo_dxf.getvalue())
            ruta_temporal = tmp_file.name

        try:
            with st.spinner("🧠 1/3 Extrayendo vectores y normalizando escala..."):
                doc = ezdxf.readfile(ruta_temporal)
                msp = doc.modelspace()
                
                muros_crudos = []
                
                # 1. Extracción con captura de polilíneas abiertas y cerradas
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

                # 2. Bounding Box Relativo y Detección de Escala
                todos_x = [m['x1'] for m in muros_crudos] + [m['x2'] for m in muros_crudos]
                todos_y = [m['y1'] for m in muros_crudos] + [m['y2'] for m in muros_crudos]
                
                min_x, max_x = min(todos_x), max(todos_x)
                min_y, max_y = min(todos_y), max(todos_y)
                dimension_mayor = max(max_x - min_x, max_y - min_y)

                if dimension_mayor < 200.0:
                    factor = 1.0       # Metros
                    unidad_origen = "Metros"
                elif dimension_mayor < 25000.0:
                    factor = 100.0     # Centímetros
                    unidad_origen = "Centímetros"
                else:
                    factor = 1000.0    # Milímetros
                    unidad_origen = "Milímetros"

                # Normalizar coordenadas restando mínimos y aplicando factor
                lineas_limpias = []
                for m in muros_crudos:
                    p1 = ((m['x1'] - min_x) / factor, (m['y1'] - min_y) / factor)
                    p2 = ((m['x2'] - min_x) / factor, (m['y2'] - min_y) / factor)
                    if p1 != p2:
                        lineas_limpias.append(LineString([p1, p2]))

            with st.spinner("🧹 2/3 Reparando esquinas y clasificando muros vs habitaciones..."):
                red_lineas = unary_union(lineas_limpias)
                
                # Snapping de tolerancia (10 cm) para sellar esquinas abiertas
                red_snapped = snap(red_lineas, red_lineas, tolerance=0.10)
                red_unida = unary_union(red_snapped)

                # Intentar poligonizar los recintos cerrados
                poligonos_crudos = list(polygonize(red_unida))
                
                # Si falló el cierre de polígonos, recurrir a la envoltura convexa
                if not poligonos_crudos:
                    poligonos_crudos = [red_unida.convex_hull]

                muros_solidos = []
                habitaciones = []

                # CLASIFICACIÓN MORFOLÓGICA (Test de Erosión)
                for poly in poligonos_crudos:
                    if not poly.is_valid or poly.area < 0.05:
                        continue
                    
                    # Si al encoger 15 cm desaparece -> Es un muro con espesor
                    nucleo_interior = poly.buffer(-0.15)
                    if nucleo_interior.is_empty:
                        muros_solidos.append(poly)
                    else:
                        habitaciones.append(poly)

                # Si el plano era de línea simple o no se separaron habitaciones:
                if not habitaciones and muros_solidos:
                    # El recinto habitable general es la envoltura de los muros
                    habitacion_principal = unary_union(muros_solidos).convex_hull
                    habitaciones.append(habitacion_principal)

                # Seleccionar la habitación principal para el reporte y aforo
                habitacion_activa = max(habitaciones, key=lambda h: h.area)
                area_habitable = round(habitacion_activa.area, 2)

                # Formatear muros para exportación (lista de polígonos o contorno exterior)
                geometria_muros_total = unary_union(muros_solidos) if muros_solidos else habitacion_activa
                
                # Extraer vértices limpios para el JSON compatible con SketchUp
                if isinstance(geometria_muros_total, MultiPolygon):
                    coords_muros = []
                    for p in geometria_muros_total.geoms:
                        coords_muros.extend([{"x": round(x, 2), "y": round(y, 2)} for x, y in p.exterior.coords])
                else:
                    coords_muros = [{"x": round(x, 2), "y": round(y, 2)} for x, y in geometria_muros_total.exterior.coords]

            with st.spinner("🚪 3/3 Calculando orientación de accesos y aforo..."):
                coords_ext = list(habitacion_activa.exterior.coords)
                mejor_segmento = None
                
                # Buscar un segmento perimetral de al menos 1 metro para colocar la puerta
                for i in range(len(coords_ext) - 1):
                    pa, pb = coords_ext[i], coords_ext[i + 1]
                    dist = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
                    if dist >= 1.0:
                        mejor_segmento = (pa, pb)
                        break

                if not mejor_segmento and len(coords_ext) >= 2:
                    mejor_segmento = (coords_ext[0], coords_ext[1])

                pa, pb = mejor_segmento
                x_puerta = (pa[0] + pb[0]) / 2.0
                y_puerta = (pa[1] + pb[1]) / 2.0
                
                dx = pb[0] - pa[0]
                dy = pb[1] - pa[1]
                rotacion_puerta = round(math.degrees(math.atan2(dy, dx)), 1)

                puertas = [{
                    "id": "puerta.skp",
                    "x": round(x_puerta, 2),
                    "y": round(y_puerta, 2),
                    "z": 0.0,
                    "rot": rotacion_puerta
                }]

                # Aforo de mesas en el área interior habitable (clearance de 1 metro)
                mesas = []
                area_interior = habitacion_activa.buffer(-1.0)
                
                if not area_interior.is_empty and area_habitable >= 6.0:
                    bx1, by1, bx2, by2 = area_interior.bounds
                    paso = 2.0
                    
                    x_c = bx1 + 0.5
                    while x_c <= bx2:
                        y_c = by1 + 0.5
                        while y_c <= by2:
                            pt = Point(x_c, y_c)
                            if area_interior.contains(pt) and pt.distance(Point(x_puerta, y_puerta)) > 1.8:
                                mesas.append({
                                    "id": "mesa.skp",
                                    "x": round(x_c, 2),
                                    "y": round(y_c, 2),
                                    "z": 0.0,
                                    "rot": 0
                                })
                            y_c += paso
                        x_c += paso

                # Generación del JSON final
                datos_proyecto = {
                    "altura_muros": 2.80,
                    "muros": coords_muros,
                    "puertas": puertas,
                    "muebles": mesas,
                    "reporte": {
                        "area_m2": area_habitable,
                        "aforo_mesas": len(mesas)
                    }
                }
                
                json_string = json.dumps(datos_proyecto, indent=4)

            st.balloons()
            st.success("🎉 ¡PROYECTO PROCESADO CON ÉXITO!")
            
            # Métricas
            col1, col2, col3 = st.columns(3)
            col1.metric("📐 Área Habitable", f"{area_habitable} m²", f"Escala: {unidad_origen}")
            col2.metric("🪑 Aforo Máximo", f"{len(mesas)} Mesas", "Clearance respetado")
            col3.metric("🚪 Giro de Puerta", f"{rotacion_puerta}°", "Alineada al muro")
            
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
st.caption("Patty Engine MVP v1.2 | Motor Topológico y Snapping")
