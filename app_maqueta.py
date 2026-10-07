import streamlit as st
import json
import os
import time

st.set_page_config(page_title="Control Maqueta - Disney", page_icon="🎢", layout="centered")

st.title("🎢 Panel de Control - Maqueta Interactiva")
st.markdown("Monitoreo en tiempo real de los puntos/personas detectadas en la maqueta.")

st.markdown("---")

# Contenedor para los datos en tiempo real
placeholder = st.empty()

# Bucle para refrescar automáticamente la interfaz cada segundo
for _ in range(1000):
    archivo_json = "estado_maqueta.json"
    
    with placeholder.container():
        if os.path.exists(archivo_json) and os.path.getsize(archivo_json) > 0:
            try:
                with open(archivo_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                nombre = data.get("show", "Atracción Maqueta")
                aforo = data.get("aforo_actual", 0)
                maximo = data.get("aforo_maximo", 10)
                porcentaje = data.get("porcentaje", 0.0)
                estado = data.get("estado", "ESTABLE")
                proxima = data.get("proxima_funcion", "10:45")
                
                st.subheader(f"📍 {nombre}")
                
                col1, col2 = st.columns([3, 1])
                
                with col1:
                    st.progress(min(aforo / maximo, 1.0))
                    st.write(f"**Puntos / Personas dentro:** {aforo} / {maximo} ({porcentaje}%)")
                
                with col2:
                    if aforo >= maximo:
                        st.error(f"🔴 **COMPLETO**\n\nPróxima función: **{proxima}**")
                    else:
                        libres = maximo - aforo
                        st.success(f"🟢 **DISPONIBLE**\n\nEspacios libres: **{libres}**")
                
                st.markdown("---")
            except json.JSONDecodeError:
                st.warning("⏳ Sincronizando datos con la maqueta...")
        else:
            st.info("⏳ Esperando que el script de la cámara (`maqueta_nodo.py`) comience a enviar datos...")
            
    time.sleep(1)
    st.rerun()