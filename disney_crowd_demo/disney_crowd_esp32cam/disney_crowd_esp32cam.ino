/*
  ============================================================
  DISNEY CROWD DEMO
  ESP32-CAM + CAMERA + 2 SERVOS
  ============================================================

  Board:
      AI Thinker ESP32-CAM

  Camera:
      AI Thinker pinout

  Servos:
      Servo 1 signal -> GPIO 13
      Servo 2 signal -> GPIO 14

  IMPORTANT:
      GPIO 13 and GPIO 14 are used here because we are NOT
      using the microSD card.

  HTTP endpoints:

      http://IP/capture

      http://IP/status

      http://IP/gate?g=1&state=open
      http://IP/gate?g=1&state=close

      http://IP/gate?g=2&state=open
      http://IP/gate?g=2&state=close

      http://IP/gates?g1=open&g2=open
      http://IP/gates?g1=close&g2=close
*/


#include <WiFi.h>
#include <WebServer.h>
#include "esp_camera.h"
#include "esp_arduino_version.h"


// ============================================================
// 1. WIFI
// ============================================================

const char* WIFI_SSID = "F_DUARTER";
const char* WIFI_PASSWORD = "lmdr1000506247";


// ============================================================
// 2. CAMERA PINOUT
// AI THINKER ESP32-CAM
// ============================================================

#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0

#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27

#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5

#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22


// ============================================================
// 3. SERVOS
// ============================================================

const int SERVO1_PIN = 13;
const int SERVO2_PIN = 14;


// Channels used for PWM.
// We use high channels so we don't interfere with camera clock.
const int SERVO1_CHANNEL = 6;
const int SERVO2_CHANNEL = 7;


const int SERVO_FREQ = 50;
const int SERVO_RESOLUTION = 16;


// SG90 pulse range
const int SERVO_MIN_US = 500;
const int SERVO_MAX_US = 2400;


// ============================================================
// SERVO ANGLES
//
// We will tune these later according to your actual barriers.
// ============================================================

const int SERVO1_CLOSED_ANGLE = 10;
const int SERVO1_OPEN_ANGLE   = 95;


// Servo 2 is mirrored physically.
const int SERVO2_CLOSED_ANGLE = 170;
const int SERVO2_OPEN_ANGLE   = 85;


bool gate1Open = false;
bool gate2Open = false;


// ============================================================
// 4. WEB SERVER
// ============================================================

WebServer server(80);


// ============================================================
// 5. SERVO FUNCTIONS
// ============================================================

uint32_t microsecondsToDuty(int us) {

  const uint32_t maxDuty =
      (1UL << SERVO_RESOLUTION) - 1;

  return (uint32_t)(
      (uint64_t)us * maxDuty / 20000UL
  );
}


// ------------------------------------------------------------
// Attach PWM to a servo
// Compatible with Arduino ESP32 Core 2.x and 3.x
// ------------------------------------------------------------

void attachServoPWM(
  int pin,
  int channel
) {

#if ESP_ARDUINO_VERSION_MAJOR >= 3

  ledcAttachChannel(
    pin,
    SERVO_FREQ,
    SERVO_RESOLUTION,
    channel
  );

#else

  ledcSetup(
    channel,
    SERVO_FREQ,
    SERVO_RESOLUTION
  );

  ledcAttachPin(
    pin,
    channel
  );

#endif
}


// ------------------------------------------------------------
// Move servo to angle
// ------------------------------------------------------------

void writeServoAngle(
  int pin,
  int channel,
  int angle
) {

  angle = constrain(
    angle,
    0,
    180
  );


  int pulseUs = map(
    angle,
    0,
    180,
    SERVO_MIN_US,
    SERVO_MAX_US
  );


  uint32_t duty =
      microsecondsToDuty(pulseUs);


#if ESP_ARDUINO_VERSION_MAJOR >= 3

  ledcWriteChannel(
    channel,
    duty
  );

#else

  ledcWrite(
    channel,
    duty
  );

#endif
}


// ------------------------------------------------------------
// Gate 1
// Zone A <-> Zone B
// ------------------------------------------------------------

void setGate1(bool openGate) {

  gate1Open = openGate;


  if (openGate) {

    writeServoAngle(
      SERVO1_PIN,
      SERVO1_CHANNEL,
      SERVO1_OPEN_ANGLE
    );

  } else {

    writeServoAngle(
      SERVO1_PIN,
      SERVO1_CHANNEL,
      SERVO1_CLOSED_ANGLE
    );
  }


  Serial.print(
    "Gate 1 A-B: "
  );

  Serial.println(
    openGate
      ? "OPEN"
      : "CLOSED"
  );
}


// ------------------------------------------------------------
// Gate 2
// Zone B <-> Zone C
// ------------------------------------------------------------

void setGate2(bool openGate) {

  gate2Open = openGate;


  if (openGate) {

    writeServoAngle(
      SERVO2_PIN,
      SERVO2_CHANNEL,
      SERVO2_OPEN_ANGLE
    );

  } else {

    writeServoAngle(
      SERVO2_PIN,
      SERVO2_CHANNEL,
      SERVO2_CLOSED_ANGLE
    );
  }


  Serial.print(
    "Gate 2 B-C: "
  );

  Serial.println(
    openGate
      ? "OPEN"
      : "CLOSED"
  );
}


// ------------------------------------------------------------
// Configure servos
// ------------------------------------------------------------

void setupServos() {

  Serial.println();
  Serial.println(
    "=== SERVO SETUP ==="
  );


  attachServoPWM(
    SERVO1_PIN,
    SERVO1_CHANNEL
  );


  attachServoPWM(
    SERVO2_PIN,
    SERVO2_CHANNEL
  );


  // Both start closed
  setGate1(false);

  delay(300);

  setGate2(false);


  Serial.println(
    "Servos initialized"
  );
}


// ============================================================
// 6. CAMERA
// ============================================================

bool setupCamera() {

  Serial.println();
  Serial.println(
    "=============================="
  );

  Serial.println(
    "=== CAMERA / MEMORY CHECK ==="
  );

  Serial.println(
    "=============================="
  );


  // ----------------------------------------------------------
  // MEMORY DIAGNOSTIC
  // ----------------------------------------------------------

  Serial.print(
    "Free heap: "
  );

  Serial.print(
    ESP.getFreeHeap()
  );

  Serial.println(
    " bytes"
  );


  bool hasPsram =
      psramFound();


  Serial.print(
    "PSRAM found: "
  );

  Serial.println(
    hasPsram
      ? "YES"
      : "NO"
  );


  if (hasPsram) {

    Serial.print(
      "PSRAM total: "
    );

    Serial.print(
      ESP.getPsramSize()
    );

    Serial.println(
      " bytes"
    );


    Serial.print(
      "PSRAM free: "
    );

    Serial.print(
      ESP.getFreePsram()
    );

    Serial.println(
      " bytes"
    );
  }


  // ----------------------------------------------------------
  // CAMERA CONFIG
  // ----------------------------------------------------------

  camera_config_t config = {};


  config.ledc_channel =
      LEDC_CHANNEL_0;

  config.ledc_timer =
      LEDC_TIMER_0;


  config.pin_d0 =
      Y2_GPIO_NUM;

  config.pin_d1 =
      Y3_GPIO_NUM;

  config.pin_d2 =
      Y4_GPIO_NUM;

  config.pin_d3 =
      Y5_GPIO_NUM;

  config.pin_d4 =
      Y6_GPIO_NUM;

  config.pin_d5 =
      Y7_GPIO_NUM;

  config.pin_d6 =
      Y8_GPIO_NUM;

  config.pin_d7 =
      Y9_GPIO_NUM;


  config.pin_xclk =
      XCLK_GPIO_NUM;

  config.pin_pclk =
      PCLK_GPIO_NUM;

  config.pin_vsync =
      VSYNC_GPIO_NUM;

  config.pin_href =
      HREF_GPIO_NUM;


  config.pin_sccb_sda =
      SIOD_GPIO_NUM;

  config.pin_sccb_scl =
      SIOC_GPIO_NUM;


  config.pin_pwdn =
      PWDN_GPIO_NUM;

  config.pin_reset =
      RESET_GPIO_NUM;


  // ----------------------------------------------------------
  // CLOCK
  // ----------------------------------------------------------

  config.xclk_freq_hz =
      20000000;


  // ----------------------------------------------------------
  // FORMAT
  // JPEG is much lighter in memory.
  // ----------------------------------------------------------

  config.pixel_format =
      PIXFORMAT_JPEG;


  // ----------------------------------------------------------
  // LOW-MEMORY TEST CONFIG
  //
  // QVGA = 320 x 240
  //
  // Once everything works we can try VGA again.
  // ----------------------------------------------------------

  config.frame_size =
      FRAMESIZE_QVGA;


  config.jpeg_quality =
      12;


  // Only ONE frame buffer.
  config.fb_count = 1;


  config.grab_mode =
      CAMERA_GRAB_WHEN_EMPTY;


  // ----------------------------------------------------------
  // MEMORY LOCATION
  // ----------------------------------------------------------

  if (hasPsram) {

    Serial.println(
      "Using PSRAM for camera buffer"
    );

    config.fb_location =
        CAMERA_FB_IN_PSRAM;

  } else {

    Serial.println(
      "Using internal DRAM for camera buffer"
    );

    config.fb_location =
        CAMERA_FB_IN_DRAM;
  }


  Serial.println();
  Serial.println(
    "Starting camera..."
  );


  // ----------------------------------------------------------
  // INITIALIZE CAMERA
  // ----------------------------------------------------------

  esp_err_t err =
      esp_camera_init(
        &config
      );


  if (
    err != ESP_OK
  ) {

    Serial.print(
      "ERROR camera init: 0x"
    );

    Serial.println(
      err,
      HEX
    );


    Serial.println(
      "Camera initialization FAILED"
    );


    return false;
  }


  Serial.println(
    "Camera initialized!"
  );


  // ----------------------------------------------------------
  // GET SENSOR
  // ----------------------------------------------------------

  sensor_t* sensor =
      esp_camera_sensor_get();


  if (
    sensor != nullptr
  ) {

    Serial.print(
      "Camera sensor PID: 0x"
    );

    Serial.println(
      sensor->id.PID,
      HEX
    );


    // If image is upside down,
    // change 0 to 1.
    sensor->set_vflip(
      sensor,
      0
    );


    // If image is mirrored,
    // change 0 to 1.
    sensor->set_hmirror(
      sensor,
      0
    );
  }


  Serial.println(
    "Camera OK"
  );


  return true;
}


// ============================================================
// 7. CAPTURE IMAGE
// ============================================================

void handleCapture() {

  camera_fb_t* fb =
      esp_camera_fb_get();


  if (
    fb == nullptr
  ) {

    Serial.println(
      "ERROR capturing camera frame"
    );


    server.send(
      500,
      "text/plain",
      "Camera capture failed"
    );


    return;
  }


  server.sendHeader(
    "Cache-Control",
    "no-store"
  );


  server.sendHeader(
    "Content-Disposition",
    "inline; filename=capture.jpg"
  );


  server.setContentLength(
    fb->len
  );


  server.send(
    200,
    "image/jpeg",
    ""
  );


  WiFiClient client =
      server.client();


  client.write(
    fb->buf,
    fb->len
  );


  esp_camera_fb_return(
    fb
  );
}


// ============================================================
// 8. WIFI
// ============================================================

void connectWiFi() {

  Serial.println();
  Serial.println(
    "=== WIFI ==="
  );


  WiFi.mode(
    WIFI_STA
  );


  WiFi.begin(
    WIFI_SSID,
    WIFI_PASSWORD
  );


  Serial.print(
    "Connecting WiFi"
  );


  unsigned long startTime =
      millis();


  while (
    WiFi.status()
        != WL_CONNECTED
  ) {

    delay(500);

    Serial.print(".");


    // After 30 seconds show warning
    if (
      millis() - startTime
        > 30000
    ) {

      Serial.println();
      Serial.println(
        "WiFi taking too long."
      );

      Serial.println(
        "Check SSID/password."
      );


      startTime =
          millis();
    }
  }


  Serial.println();
  Serial.println(
    "WiFi connected!"
  );


  Serial.print(
    "ESP32-CAM IP: "
  );


  Serial.println(
    WiFi.localIP()
  );


  Serial.print(
    "WiFi RSSI: "
  );


  Serial.println(
    WiFi.RSSI()
  );
}


// ============================================================
// 9. HTTP SERVO COMMANDS
// ============================================================

bool parseGateState(
  String value,
  bool& result
) {

  value.toLowerCase();


  if (
    value == "open" ||
    value == "1" ||
    value == "on"
  ) {

    result = true;

    return true;
  }


  if (
    value == "close" ||
    value == "closed" ||
    value == "0" ||
    value == "off"
  ) {

    result = false;

    return true;
  }


  return false;
}


// ------------------------------------------------------------
// ONE GATE
// ------------------------------------------------------------

void handleGate() {

  if (
    !server.hasArg("g") ||
    !server.hasArg("state")
  ) {

    server.send(
      400,
      "application/json",
      "{\"ok\":false,\"error\":\"Use /gate?g=1&state=open\"}"
    );


    return;
  }


  int gate =
      server.arg("g").toInt();


  bool openGate;


  if (
    !parseGateState(
      server.arg("state"),
      openGate
    )
  ) {

    server.send(
      400,
      "application/json",
      "{\"ok\":false,\"error\":\"state must be open or close\"}"
    );


    return;
  }


  if (
    gate == 1
  ) {

    setGate1(
      openGate
    );

  } else if (
    gate == 2
  ) {

    setGate2(
      openGate
    );

  } else {

    server.send(
      400,
      "application/json",
      "{\"ok\":false,\"error\":\"gate must be 1 or 2\"}"
    );


    return;
  }


  server.send(
    200,
    "application/json",
    "{\"ok\":true}"
  );
}


// ------------------------------------------------------------
// BOTH GATES
// ------------------------------------------------------------

void handleGates() {

  bool newGate1 =
      gate1Open;


  bool newGate2 =
      gate2Open;


  if (
    server.hasArg("g1")
  ) {

    if (
      !parseGateState(
        server.arg("g1"),
        newGate1
      )
    ) {

      server.send(
        400,
        "application/json",
        "{\"ok\":false,\"error\":\"invalid g1\"}"
      );


      return;
    }
  }


  if (
    server.hasArg("g2")
  ) {

    if (
      !parseGateState(
        server.arg("g2"),
        newGate2
      )
    ) {

      server.send(
        400,
        "application/json",
        "{\"ok\":false,\"error\":\"invalid g2\"}"
      );


      return;
    }
  }


  setGate1(
    newGate1
  );


  setGate2(
    newGate2
  );


  server.send(
    200,
    "application/json",
    "{\"ok\":true}"
  );
}


// ============================================================
// 10. STATUS
// ============================================================

void handleStatus() {

  String json = "{";


  json +=
      "\"ok\":true,";


  json +=
      "\"ip\":\"";

  json +=
      WiFi.localIP().toString();

  json +=
      "\",";


  json +=
      "\"gate1\":\"";

  json +=
      gate1Open
        ? "open"
        : "closed";

  json +=
      "\",";


  json +=
      "\"gate2\":\"";

  json +=
      gate2Open
        ? "open"
        : "closed";

  json +=
      "\",";


  json +=
      "\"psram\":";

  json +=
      psramFound()
        ? "true"
        : "false";

  json +=
      ",";


  json +=
      "\"freeHeap\":";

  json +=
      String(
        ESP.getFreeHeap()
      );

  json +=
      ",";


  json +=
      "\"rssi\":";

  json +=
      String(
        WiFi.RSSI()
      );


  json += "}";


  server.send(
    200,
    "application/json",
    json
  );
}


// ============================================================
// 11. ROOT PAGE
// ============================================================

void handleRoot() {

  String message;


  message +=
      "Disney Crowd Demo ESP32-CAM\n\n";


  message +=
      "Camera:\n";

  message +=
      "/capture\n\n";


  message +=
      "Status:\n";

  message +=
      "/status\n\n";


  message +=
      "Servo 1 OPEN:\n";

  message +=
      "/gate?g=1&state=open\n\n";


  message +=
      "Servo 1 CLOSE:\n";

  message +=
      "/gate?g=1&state=close\n\n";


  message +=
      "Servo 2 OPEN:\n";

  message +=
      "/gate?g=2&state=open\n\n";


  message +=
      "Servo 2 CLOSE:\n";

  message +=
      "/gate?g=2&state=close\n\n";


  server.send(
    200,
    "text/plain",
    message
  );
}


// ============================================================
// 12. WEB SERVER
// ============================================================

void setupServer() {

  server.on(
    "/",
    HTTP_GET,
    handleRoot
  );


  server.on(
    "/capture",
    HTTP_GET,
    handleCapture
  );


  server.on(
    "/status",
    HTTP_GET,
    handleStatus
  );


  server.on(
    "/gate",
    HTTP_GET,
    handleGate
  );


  server.on(
    "/gates",
    HTTP_GET,
    handleGates
  );


  server.begin();


  Serial.println();
  Serial.println(
    "HTTP server started"
  );
}


// ============================================================
// 13. SETUP
// ============================================================

void setup() {

  Serial.begin(
    115200
  );


  delay(
    1500
  );


  Serial.println();
  Serial.println();
  Serial.println(
    "================================"
  );

  Serial.println(
    "=== DISNEY CROWD DEMO START ==="
  );

  Serial.println(
    "================================"
  );


  // ----------------------------------------------------------
  // WIFI
  // ----------------------------------------------------------

  connectWiFi();


  // ----------------------------------------------------------
  // CAMERA
  // ----------------------------------------------------------

  bool cameraOK =
      setupCamera();


  if (
    !cameraOK
  ) {

    Serial.println();
    Serial.println(
      "!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    );

    Serial.println(
      "CAMERA FAILED"
    );

    Serial.println(
      "Stopping here."
    );

    Serial.println(
      "!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    );


    while (
      true
    ) {

      delay(
        1000
      );
    }
  }


  // ----------------------------------------------------------
  // SERVOS
  // ----------------------------------------------------------

  setupServos();


  // ----------------------------------------------------------
  // SERVER
  // ----------------------------------------------------------

  setupServer();


  // ----------------------------------------------------------
  // READY
  // ----------------------------------------------------------

  Serial.println();
  Serial.println(
    "================================"
  );

  Serial.println(
    "SYSTEM READY"
  );

  Serial.println(
    "================================"
  );


  Serial.println();


  Serial.print(
    "Camera: http://"
  );

  Serial.print(
    WiFi.localIP()
  );

  Serial.println(
    "/capture"
  );


  Serial.print(
    "Status: http://"
  );

  Serial.print(
    WiFi.localIP()
  );

  Serial.println(
    "/status"
  );


  Serial.print(
    "Servo 1 test: http://"
  );

  Serial.print(
    WiFi.localIP()
  );

  Serial.println(
    "/gate?g=1&state=open"
  );


  Serial.print(
    "Servo 2 test: http://"
  );

  Serial.print(
    WiFi.localIP()
  );

  Serial.println(
    "/gate?g=2&state=open"
  );


  Serial.println();
}


// ============================================================
// 14. LOOP
// ============================================================

void loop() {

  server.handleClient();

  delay(
    2
  );
}