import streamlit as st
import ezdxf
import json
import tempfile
import os
from shapely.geometry import LineString, MultiLineString, Point

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
                # Leer plano y aplanar Z
                for e in msp.query('LINE LWPOLYLINE'):
                    if e.dxftype() == 'LINE':
                        muros_crudos.append({
                            "x1": float(e.dxf.start.x), "y1": float(e.dxf.start.y),
                            "x2": float(e.dxf.end.x), "y2": float(e.dxf.end.y)
                        })
                    elif e.dxftype() == 'LWPOLYLINE':
                        pts = [(float(p[0]), float(p[1])) for p in e.get_points('xy')]
                        for i in range(len(pts)-1):
                            muros_crudos.append({
                                "x1": pts[i][0], "y1": pts[i][1],
                                "x2": pts[i+1][0], "y2": pts[i+1][1]
                            })

                if not muros_crudos:
                    st.error("❌ No se encontraron líneas válidas en el plano.")
                    st.stop()

                # IA DE AUTO-ESCALA
                max_val = max(max(abs(m['x1']), abs(m['x2'])) for m in muros_crudos)
                factor = 1.0
                if max_val > 5000:
                    factor = 1000.0 # Estaba en milímetros
                elif max_val > 500:
                    factor = 100.0 # Estaba en centímetros

                lineas_limpias = []
                for m in muros_crudos:
                    p1 = (m['x1']/factor, m['y1']/factor)
                    p2 = (m['x2']/factor, m['y2']/factor)
                    lineas_limpias.append(LineString([p1, p2]))

            with st.spinner("🧹 Purgando basura y calculando aforo..."):
                geometria = MultiLineString(lineas_limpias)
                poligono_local = geometria.convex_hull

                coordenadas_muros = [{"x": round(x, 2), "y": round(y, 2)} for x, y in poligono_local.exterior.coords]
                
                # Simulador de Marcador de Puerta
                x_puerta = (coordenadas_muros[0]['x'] + coordenadas_muros[1]['x']) / 2
                y_puerta = (coordenadas_muros[0]['y'] + coordenadas_muros[1]['y']) / 2
                puertas = [{"id": "puerta.skp", "x": round(x_puerta, 2), "y": round(y_puerta, 2), "z": 0.0, "rot": 0}]

                # Acomodo de mesas
                mesas = []
                minx, miny, maxx, maxy = poligono_local.bounds
                x_actual = minx + 1.5
                while x_actual < maxx - 1.5:
                    y_actual = miny + 1.5
                    while y_actual < maxy - 1.5:
                        punto = Point(x_actual, y_actual)
                        distancia_puerta = punto.distance(Point(x_puerta, y_puerta))
                        if poligono_local.contains(punto) and distancia_puerta > 2.0:
                            mesas.append({"id": "mesa.skp", "x": round(x_actual, 2), "y": round(y_actual, 2), "z": 0.0, "rot": 0})
                        y_actual += 2.0
                    x_actual += 2.0

                area_local = round(poligono_local.area, 2)
                
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
            
            # Mostrar la matemática real calculada en vivo
            col1, col2 = st.columns(2)
            col1.metric("📐 Área Detectada", f"{area_local} m²", "Escala corregida")
            col2.metric("🪑 Aforo Máximo", f"{len(mesas)} Mesas", "Distribución óptima")
            
            st.markdown("---")
            st.markdown("### 📥 Descarga tus archivos puente:")
            
            # Botón REAL que descarga el JSON recién calculado
            st.download_button(
                label="📦 Descargar proyecto.json (Para SketchUp)", 
                data=json_string, 
                file_name="proyecto.json", 
                mime="application/json"
            )

        except Exception as e:
            st.error(f"❌ Error al procesar el plano: {e}")
        
        finally:
            # Borramos el rastro del archivo en el servidor para ahorrar memoria
            os.remove(ruta_temporal) 

st.markdown("---")
st.caption("Patty Engine MVP v1.0 | Desarrollado para revolucionar la arquitectura.")
