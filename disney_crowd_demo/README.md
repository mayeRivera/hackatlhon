# Disney Crowd Demo — ESP32-CAM + 2 servos + Python

## Qué hace

- Un solo ESP32-CAM toma fotos de la maqueta.
- El mismo ESP32-CAM controla dos servos:
  - Gate 1: Zona A ↔ Zona B
  - Gate 2: Zona B ↔ Zona C
- Python recibe las fotos, cuenta las 10 figuras por zona y decide:
  - NORMAL
  - WARNING
  - REDIRECT
  - SECURITY
- El dashboard muestra cámara, conteos, alarmas y estado de las barreras.

---

# 1. Cableado

## Cámara
La cámara se conecta al conector FPC de la ESP32-CAM.

## Servo 1
- Signal naranja/amarillo → GPIO 13
- Rojo → +5 V de fuente externa
- Marrón/negro → GND de fuente externa

## Servo 2
- Signal naranja/amarillo → GPIO 14
- Rojo → +5 V de fuente externa
- Marrón/negro → GND de fuente externa

## MUY IMPORTANTE
Unir:
- GND de fuente externa
- GND de ESP32-CAM

No alimentar ambos servos desde 3.3 V del ESP32-CAM.

Recomendación:
- Fuente 5 V / 2 A
- Condensador 470–1000 µF entre +5 V y GND cerca de los servos.

GPIO 13 y GPIO 14 se usan también para microSD, por eso NO usar microSD en esta demo.

---

# 2. Arduino IDE

1. Instalar Arduino IDE.
2. En Board Manager instalar `esp32` by Espressif Systems.
3. Insertar ESP32-CAM en ESP32-CAM-MB.
4. Seleccionar:
   - Board: `AI Thinker ESP32-CAM`
   - Upload Speed: 115200
   - Partition Scheme: Huge APP (si está disponible)
5. Abrir `disney_crowd_esp32cam.ino`.
6. Cambiar:
   - TU_WIFI
   - TU_PASSWORD
7. Subir.
8. Si no entra en programación:
   - mantener IO0 presionado,
   - pulsar RST,
   - iniciar Upload,
   - soltar IO0 cuando comience.
9. Al terminar, pulsar RST normalmente.
10. Abrir Serial Monitor a 115200.
11. Copiar la IP que aparece, por ejemplo:
   `ESP32-CAM IP: 192.168.1.88`

Pruebas en navegador:
- `http://192.168.1.88/capture`
- `http://192.168.1.88/status`
- `http://192.168.1.88/gate?g=1&state=open`
- `http://192.168.1.88/gate?g=1&state=close`

---

# 3. Python

En PC o Jetson:

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

Linux/Jetson:

```bash
source .venv/bin/activate
```

Instalar:

```bash
pip install -r requirements.txt
```

Ejecutar:

```bash
python crowd_control_demo.py --esp32 192.168.1.88
```

Abrir:

`http://127.0.0.1:5000`

---

# 4. Primera calibración

## Paso A — fondo
1. Fijar la cámara.
2. Quitar TODOS los muñecos.
3. No mover la maqueta.
4. En dashboard pulsar:
   `Capture EMPTY map`.

## Paso B — figuras
1. Poner los 10 muñecos distribuidos en las zonas.
2. Retirar la mano de la imagen.
3. Pulsar:
   `Calibrate 10 figures`.

Después de esto puedes mover los 10 muñecos entre zonas.

La cámara NO debe moverse después de capturar el fondo.

---

# 5. Umbrales de demo

En `crowd_control_demo.py`:

```python
ZONE_WARNING = 4
ZONE_FULL = 5
SECURITY_MIN_EACH = 3
```

Ejemplos con 10 muñecos:

- 6 / 2 / 2 → redirige desde A
- 2 / 6 / 2 → abre ambos pasos desde B si ambos lados están disponibles
- 2 / 2 / 6 → redirige desde C
- 3 / 4 / 3 → SECURITY: todas las zonas tienen ocupación relevante

Son valores para la maqueta, NO capacidades reales del parque.

---

# 6. Ajustar posición de servos

Antes de poner los palitos de las barreras, probar los motores solos.

En Arduino:

```cpp
SERVO1_CLOSED_ANGLE
SERVO1_OPEN_ANGLE
SERVO2_CLOSED_ANGLE
SERVO2_OPEN_ANGLE
```

Cambiar los grados hasta que las barreras queden bien.

---

# 7. Si las zonas dibujadas no coinciden con el mapa

Modificar al inicio de Python:

```python
ZONE_POLYGONS_NORM
```

El dashboard dibuja los bordes de A, B y C sobre la imagen, por lo que es fácil verificar la alineación.

---

# 8. Consejos para Demo Day

- Cámara en alto y fija.
- Luz constante.
- No meter manos mientras el sistema está contando.
- Separar un poco los muñecos.
- No mover la maqueta después de calibrar el fondo.
- Llevar una fuente 5 V / 2 A para los servos.
- Tener a mano botones manuales del dashboard por si quieren probar los servos sin depender del conteo.
