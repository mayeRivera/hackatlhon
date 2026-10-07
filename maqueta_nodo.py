import cv2
import duckdb
import time
import json

# --- CONFIGURAÇÃO DA MAQUETA ---
NOMBRE_SHOW = "Space Mountain (Maqueta)"
AFORO_MAXIMO = 10                         
DURACION_SHOW_MINUTOS = 15                
ARCHIVO_ESTADO = "estado_maqueta.json"    

con = duckdb.connect(database=':memory:', read_only=False)
con.execute("""
    CREATE TABLE show_flow_events (
        event_id INTEGER,
        checkpoint VARCHAR,
        timestamp DOUBLE
    )
""")

cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("[ERROR] Não foi possível abrir a câmara.")
    exit()

print(f"[INFO] Sistema de Maqueta para '{NOMBRE_SHOW}' iniciado com contagem automática de pontos.")

def actualizar_estado_app(aforo, maximo, estado, proxima_hora=""):
    data = {
        "show": NOMBRE_SHOW,
        "aforo_actual": aforo,
        "aforo_maximo": maximo,
        "porcentaje": round((aforo / maximo) * 100, 1),
        "estado": estado,
        "proxima_funcion": proxima_hora,
        "actualizado_en": time.strftime("%H:%M:%S")
    }
    with open(ARCHIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

while True:
    ret, frame2 = cap.read()
    if not ret:
        break

    # Converter para o espaço de cor HSV para detetar os pontos verdes com precisão
    hsv = cv2.cvtColor(frame2, cv2.COLOR_BGR2HSV)

    # Intervalo de cor verde
    lower_green = (35, 50, 50)
    upper_green = (85, 255, 255)

    mask = cv2.inRange(hsv, lower_green, upper_green)
    mask = cv2.erode(mask, None, iterations=1)
    mask = cv2.dilate(mask, None, iterations=2)

    contours, _ = cv2.findContours(mask.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    puntos_detectados = 0
    for contour in contours:
        if cv2.contourArea(contour) > 100:
            puntos_detectados += 1
            (x, y, w, h) = cv2.boundingRect(contour)
            centro_x = x + w // 2
            centro_y = y + h // 2
            cv2.circle(frame2, (centro_x, centro_y), 12, (0, 255, 255), 2)

    # O aforo actual é diretamente o número de pontos verdes detetados na imagem
    aforo_actual = min(puntos_detectados, AFORO_MAXIMO)

    estado_alerta = "ESTABLE"
    color_texto = (0, 255, 0)
    proxima_hora_texto = ""

    if aforo_actual >= AFORO_MAXIMO:
        estado_alerta = "¡AFORO LLENO!"
        color_texto = (0, 0, 255)
        proxima_hora_texto = "10:45"

    actualizar_estado_app(aforo_actual, AFORO_MAXIMO, estado_alerta, proxima_hora_texto)

    # Mostrar métricas na janela da câmara
    cv2.putText(frame2, f"Aforo Maqueta: {aforo_actual} / {AFORO_MAXIMO}", (30, 40), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color_texto, 2, cv2.LINE_AA)
    cv2.putText(frame2, f"Puntos Verdes: {puntos_detectados}", (30, 80), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(frame2, "Pressiona 'q' para sair", (30, 120), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

    cv2.imshow("Maqueta - Control de Puntos", frame2)

    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
