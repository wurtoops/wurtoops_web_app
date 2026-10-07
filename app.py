import streamlit as st
import ezdxf
import json
import tempfile
import os
import math
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.ops import unary_union, polygonize

# Configuración de la pestaña
st.set_page_config(page_title="Patty | Generador Arquitectónico", page_icon="🏗️", layout="centered")

st.title("🏗️ Patty - Motor de Automatización 2D a 3D")
st.subheader("Transforma tus planos de CAD a modelos 3D y presupuestos en segundos.")
st.markdown("---")

# Zona de arrastre
archivo_dxf = st.file_uploader("📥 Arrastra aquí el plano de tu cliente (.DXF)", type=['dxf'])

if archivo_dxf is not None:
    st.success(f"✅ Archivo '{archivo_dxf.name}' cargado con éxito.")
    
    if st.button("🚀 Iniciar Generación Automática"):
        
        # 1. Guardar temporalmente el archivo en el servidor
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp_file:
            tmp_file.write(archivo_dxf.getvalue())
            ruta_temporal = tmp_file.name

        try:
            with st.spinner("🧠 Analizando geometría y corrigiendo escalas..."):
                doc = ezdxf.readfile(ruta_temporal)
                msp = doc.modelspace()
                
                muros_crudos = []
                
                # 2. Extracción precisa de líneas y polilíneas (con soporte para polilíneas cerradas)
                for e in msp.query('LINE LWPOLYLINE'):
                    if e.dxftype() == 'LINE':
                        muros_crudos.append({
                            "x1": float(e.dxf.start.x), "y1": float(e.dxf.start.y),
                            "x2": float(e.dxf.end.x), "y2": float(e.dxf.end.y)
                        })
                    elif e.dxftype() == 'LWPOLYLINE':
                        pts = [(float(p[0]), float(p[1])) for p in e.get_points('xy')]
                        if len(pts) > 1:
                            for i in range(len(pts) - 1):
                                muros_crudos.append({
                                    "x1": pts[i][0], "y1": pts[i][1],
                                    "x2": pts[i+1][0], "y2": pts[i+1][1]
                                })
                            # Si la polilínea está cerrada, conectar el último vértice con el primero
                            if getattr(e, 'is_closed', False) or getattr(e, 'closed', False):
                                muros_crudos.append({
                                    "x1": pts[-1][0], "y1": pts[-1][1],
                                    "x2": pts[0][0], "y2": pts[0][1]
                                })

                if not muros_crudos:
                    st.error("❌ No se encontraron líneas válidas en el plano.")
                    st.stop()

                # 3. AUTO-ESCALA BASADA EN BOUNDING BOX RELATIVO (DELTA)
                todos_x = [m['x1'] for m in muros_crudos] + [m['x2'] for m in muros_crudos]
                todos_y = [m['y1'] for m in muros_crudos] + [m['y2'] for m in muros_crudos]
                
                min_x, max_x = min(todos_x), max(todos_x)
                min_y, max_y = min(todos_y), max(todos_y)
                
                delta_x = max_x - min_x
                delta_y = max_y - min_y
                dimension_mayor = max(delta_x, delta_y)

                # Heurística de detección de escala arquitectónica
                if dimension_mayor < 200.0:
                    factor = 1.0       # Ya está en metros (ej. tu habitación de ~9.6m)
                    unidad_origen = "Metros"
                elif dimension_mayor < 25000.0:
                    factor = 100.0     # Estaba en centímetros
                    unidad_origen = "Centímetros"
                else:
                    factor = 1000.0    # Estaba en milímetros
                    unidad_origen = "Milímetros"

                # 4. NORMALIZACIÓN AL ORIGEN (0,0) Y CONVERSIÓN A METROS
                lineas_limpias = []
                for m in muros_crudos:
                    # Se resta el mínimo para anclar en 0,0 y se divide por el factor
                    p1 = ((m['x1'] - min_x) / factor, (m['y1'] - min_y) / factor)
                    p2 = ((m['x2'] - min_x) / factor, (m['y2'] - min_y) / factor)
                    
                    # Evitar micro-segmentos con longitud cero
                    if p1 != p2:
                        lineas_limpias.append(LineString([p1, p2]))

            with st.spinner("🧹 Construyendo recintos y calculando aforo..."):
                geometria_unida = unary_union(lineas_limpias)
                
                # Intentar poligonización topológica limpia (recintos reales)
                poligonos_posibles = list(polygonize(geometria_unida))
                
                if poligonos_posibles:
                    # Tomar el recinto principal
                    poligono_local = max(poligonos_posibles, key=lambda p: p.area)
                else:
                    # Respaldo de seguridad si las esquinas del CAD están abiertas
                    poligono_local = geometria_unida.convex_hull

                coordenadas_muros = [{"x": round(x, 2), "y": round(y, 2)} for x, y in poligono_local.exterior.coords]
                
                # 5. CÁLCULO INTELIGENTE DE PUERTA (CON ÁNGULO Y ORIENTACIÓN)
                coords_ext = list(poligono_local.exterior.coords)
                mejor_segmento = None
                
                # Buscar un segmento perimetral adecuado para una puerta (0.8m a 2.5m)
                for i in range(len(coords_ext) - 1):
                    p_a = coords_ext[i]
                    p_b = coords_ext[i + 1]
                    dist_seg = math.hypot(p_b[0] - p_a[0], p_b[1] - p_a[1])
                    if 0.8 <= dist_seg <= 3.0:
                        mejor_segmento = (p_a, p_b)
                        break
                
                # Si no hay segmento específico, usar el primero como fallback
                if not mejor_segmento and len(coords_ext) >= 2:
                    mejor_segmento = (coords_ext[0], coords_ext[1])

                p_ini, p_fin = mejor_segmento
                x_puerta = (p_ini[0] + p_fin[0]) / 2.0
                y_puerta = (p_ini[1] + p_fin[1]) / 2.0
                
                # Cálculo del ángulo real del muro en grados
                dx_muro = p_fin[0] - p_ini[0]
                dy_muro = p_fin[1] - p_ini[1]
                angulo_rad = math.atan2(dy_muro, dx_muro)
                angulo_grados = round(math.degrees(angulo_rad), 1)

                puertas = [{
                    "id": "puerta.skp",
                    "x": round(x_puerta, 2),
                    "y": round(y_puerta, 2),
                    "z": 0.0,
                    "rot": angulo_grados
                }]

                # 6. ACOMODO DE MESAS PROTEGIDO (CLEARANCE PERIMETRAL)
                mesas = []
                area_local = round(poligono_local.area, 2)
                
                # Solo calcular aforo si el área es habitable (> 4 m2)
                if area_local >= 4.0:
                    area_restringida = poligono_local.buffer(-0.8) # 80cm de distancia a las paredes
                    
                    if not area_restringida.is_empty:
                        bx1, by1, bx2, by2 = area_restringida.bounds
                        paso = 2.0  # Cuadrícula de 2 metros entre mesas
                        
                        x_curr = bx1 + 0.5
                        while x_curr <= bx2:
                            y_curr = by1 + 0.5
                            while y_curr <= by2:
                                pt = Point(x_curr, y_curr)
                                dist_puerta = pt.distance(Point(x_puerta, y_puerta))
                                
                                if area_restringida.contains(pt) and dist_puerta > 1.8:
                                    mesas.append({
                                        "id": "mesa.skp",
                                        "x": round(x_curr, 2),
                                        "y": round(y_curr, 2),
                                        "z": 0.0,
                                        "rot": 0
                                    })
                                y_curr += paso
                            x_curr += paso

                # 7. GENERACIÓN DEL JSON CANÓNICO
                datos_proyecto = {
                    "altura_muros": 2.80,
                    "muros": coordenadas_muros,
                    "puertas": puertas,
                    "muebles": mesas,
                    "reporte": {
                        "area_m2": area_local,
                        "aforo_mesas": len(mesas)
                    }
                }
                
                json_string = json.dumps(datos_proyecto, indent=4)

            st.balloons()
            st.success("🎉 ¡PROYECTO PROCESADO CON ÉXITO!")
            
            # Métricas en vivo
            col1, col2, col3 = st.columns(3)
            col1.metric("📐 Área Detectada", f"{area_local} m²", f"Origen: {unidad_origen}")
            col2.metric("🪑 Aforo de Mesas", f"{len(mesas)} Mesas", "Distribución óptima")
            col3.metric("🚪 Orientación Puerta", f"{angulo_grados}°", "Alineada al muro")
            
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
st.caption("Patty Engine MVP v1.1 | Geometría y Escala Corregidas")
