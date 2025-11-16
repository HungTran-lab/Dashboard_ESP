#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_ADS1X15.h>
#include <Adafruit_NeoPixel.h>

#include "pins.h"

// ===== OLED SSD1306 128x64 =====
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
bool OLED_OK = false;

// Nội dung 2 dòng trên OLED
String oled_l1 = "ESP32 KIT";
String oled_l2 = "-READY-";

// ===== ADS1115 =====
Adafruit_ADS1115 ads;
bool ADS_OK = false;

// ===== WS2812 (1 LED) =====
Adafruit_NeoPixel strip(NUMPIXELS, WS2812_PIN, NEO_GRB + NEO_KHZ800);

// ===== Buzzer =====
void beep(uint16_t on_ms = 80) 
{
  digitalWrite(BUZZER_PIN, HIGH);
  delay(on_ms);
  digitalWrite(BUZZER_PIN, LOW);
}

// ===== Update nội dung OLED (vẽ lại cả 2 dòng) =====
void oledRender() 
{
  if (!OLED_OK) return;

  display.clearDisplay();
  display.setTextSize(2);
  display.setTextColor(SSD1306_WHITE);

  // Hàng 1
  display.setCursor(0, 5);
  display.println(oled_l1);

  // Hàng 2
  display.setCursor(0, 40);
  display.println(oled_l2);

  display.display();
}

// ===== Khởi tạo IO =====
void setupPins() 
{
  // Relay outputs
  pinMode(RELAY1, OUTPUT);
  pinMode(RELAY2, OUTPUT);
  pinMode(RELAY3, OUTPUT);
  pinMode(RELAY4, OUTPUT);

  // Buzzer + spare outputs
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(SPARE1, OUTPUT);
  pinMode(SPARE2, OUTPUT);    // LED on-board (LED ON/OFF)


    // ==== I/O SPARE ====
  pinMode(SIO1, OUTPUT);
  pinMode(SIO2, OUTPUT);
  pinMode(SIO3, OUTPUT);
  // pinMode(SIO4, OUTPUT);


  // Đặt trạng thái mặc định
  digitalWrite(RELAY1, LOW);
  digitalWrite(RELAY2, LOW);
  digitalWrite(RELAY3, LOW);
  digitalWrite(RELAY4, LOW);
  digitalWrite(BUZZER_PIN, LOW);

  digitalWrite(SPARE1, LOW);
  // digitalWrite(SPARE2, LOW);

  digitalWrite(SIO1, LOW);
  digitalWrite(SIO2, LOW);
  digitalWrite(SIO3, LOW);
  //digitalWrite(SIO4, LOW);

  // Digital inputs
  pinMode(SENSOR1, INPUT);
  pinMode(SENSOR2, INPUT);
  pinMode(SENSOR3, INPUT);
  pinMode(SENSOR4, INPUT);
  pinMode(SENSOR5, INPUT);

  // Analog: ADC1, ADC2, ADC3 dùng analogRead trực tiếp
}

// ===== Gửi STATUS cho PC =====
// CHỈ gửi ADC nội & Sensor, KHÔNG đọc ADS ở đây
// Format: STATUS;ADC=a1,a2,a3;S=s1,s2,s3,s4,s5;
void sendStatus() 
{
  int adc1 = analogRead(ADC1);
  int adc2 = analogRead(ADC2);
  int adc3 = analogRead(ADC3);

  int s1 = digitalRead(SENSOR1);
  int s2 = digitalRead(SENSOR2);
  int s3 = digitalRead(SENSOR3);
  int s4 = digitalRead(SENSOR4);
  int s5 = digitalRead(SENSOR5);

  Serial.print("STATUS;");
  Serial.print("ADC=");
  Serial.print(adc1); Serial.print(",");
  Serial.print(adc2); Serial.print(",");
  Serial.print(adc3);
  Serial.print(";S=");
  Serial.print(s1); Serial.print(",");
  Serial.print(s2); Serial.print(",");
  Serial.print(s3); Serial.print(",");
  Serial.print(s4); Serial.print(",");
  Serial.print(s5);
  Serial.println(";");
}

// ===== Gửi giá trị ADS1115 A0/A1 cho PC =====
// Format: ADS;A0=xxxx;A1=yyyy;
void sendAds() 
{
  int16_t ads0 = 0;
  int16_t ads1 = 0;

  if (ADS_OK) 
  {
    ads0 = ads.readADC_SingleEnded(0);   // kênh A0
    ads1 = ads.readADC_SingleEnded(1);   // kênh A1
  }

  Serial.print("ADS;");
  Serial.print("A0=");
  Serial.print(ads0);
  Serial.print(";A1=");
  Serial.print(ads1);
  Serial.println(";");
}

// ===== Điều khiển WS2812 =====
void setRGB(uint8_t r, uint8_t g, uint8_t b) 
{
  strip.setPixelColor(0, strip.Color(r, g, b));  // NEO_GRB
  strip.show();
}

// ===== Xử lý 1 lệnh từ PC =====
void handleCommand(const String& cmd_in) 
{
  String raw = cmd_in;
  raw.trim();

  String c = raw;
  c.trim();
  c.toUpperCase();

  if (c.length() == 0) return;

  // --- Lệnh đơn giản ---
  if (c == "PING") 
  {
    Serial.println("PONG");
    return;
  }

  if (c == "INFO") 
  {
    Serial.println("KIT=ESP32;FW=1.4;");   // tăng version vì thêm lệnh ADS riêng
    return;
  }

  if (c == "BUZ") 
  {
    beep(120);
    Serial.println("OK;BUZ;");
    return;
  }

  if (c == "READ") 
  {
    // chỉ đọc ADC nội + Sensor
    sendStatus();
    return;
  }

  if (c == "ADS") 
  {
    // chỉ đọc ADS1115 khi PC yêu cầu
    sendAds();
    return;
  }

  // --- Relay R1..R4 ON/OFF ---
  if (c.startsWith("R1 ")) 
  {
    if (c.endsWith("ON"))  { digitalWrite(RELAY1, HIGH); Serial.println("OK;R1=ON;"); }
    if (c.endsWith("OFF")) { digitalWrite(RELAY1, LOW);  Serial.println("OK;R1=OFF;"); }
    return;
  }
  if (c.startsWith("R2 ")) 
  {
    if (c.endsWith("ON"))  { digitalWrite(RELAY2, HIGH); Serial.println("OK;R2=ON;"); }
    if (c.endsWith("OFF")) { digitalWrite(RELAY2, LOW);  Serial.println("OK;R2=OFF;"); }
    return;
  }
  if (c.startsWith("R3 ")) 
  {
    if (c.endsWith("ON"))  { digitalWrite(RELAY3, HIGH); Serial.println("OK;R3=ON;"); }
    if (c.endsWith("OFF")) { digitalWrite(RELAY3, LOW);  Serial.println("OK;R3=OFF;"); }
    return;
  }
  if (c.startsWith("R4 ")) 
  {
    if (c.endsWith("ON"))  { digitalWrite(RELAY4, HIGH); Serial.println("OK;R4=ON;"); }
    if (c.endsWith("OFF")) { digitalWrite(RELAY4, LOW);  Serial.println("OK;R4=OFF;"); }
    return;
  }

  // --- I/O SPARE: SIO1..SIO4 ON/OFF ---
  if (c.startsWith("SIO1 ")) 
  {
    if (c.endsWith("ON"))  { digitalWrite(SIO1, HIGH); Serial.println("OK;SIO1=ON;"); }
    if (c.endsWith("OFF")) { digitalWrite(SIO1, LOW);  Serial.println("OK;SIO1=OFF;"); }
    return;
  }
  if (c.startsWith("SIO2 ")) 
  {
    if (c.endsWith("ON"))  { digitalWrite(SIO2, HIGH); Serial.println("OK;SIO2=ON;"); }
    if (c.endsWith("OFF")) { digitalWrite(SIO2, LOW);  Serial.println("OK;SIO2=OFF;"); }
    return;
  }
  if (c.startsWith("SIO3 ")) 
  {
    if (c.endsWith("ON"))  { digitalWrite(SIO3, HIGH); Serial.println("OK;SIO3=ON;"); }
    if (c.endsWith("OFF")) { digitalWrite(SIO3, LOW);  Serial.println("OK;SIO3=OFF;"); }
    return;
  }

  // --- LED test trên SIO2 (GPIO2) ---
  if (c == "LED ON") 
  {
    digitalWrite(SPARE1, HIGH);
    Serial.println("OK;LED=ON;");
    return;
  }
  if (c == "LED OFF") 
  {
    digitalWrite(SPARE1, LOW);
    Serial.println("OK;LED=OFF;");
    return;
  }

  // --- WS2812: RGB R,G,B ---
  if (c.startsWith("RGB")) 
  {
    int spaceIndex = raw.indexOf(' ');
    if (spaceIndex > 0 && spaceIndex < (int)raw.length() - 1) {
      String params = raw.substring(spaceIndex + 1); // "R,G,B"
      params.trim();

      int firstComma  = params.indexOf(',');
      int secondComma = params.indexOf(',', firstComma + 1);

      if (firstComma > 0 && secondComma > firstComma) {
        uint8_t r = (uint8_t) params.substring(0, firstComma).toInt();
        uint8_t g = (uint8_t) params.substring(firstComma + 1, secondComma).toInt();
        uint8_t b = (uint8_t) params.substring(secondComma + 1).toInt();

        setRGB(r, g, b);

        Serial.print("OK;RGB=");
        Serial.print(r); Serial.print(",");
        Serial.print(g); Serial.print(",");
        Serial.print(b); Serial.println(";");
        return;
      }
    }
    Serial.println("ERR;BAD_RGB;");
    return;
  }

  // --- OLED: Hàng 1 & Hàng 2 ---
  if (c.startsWith("OL1 ")) 
  {
    int spaceIndex = raw.indexOf(' ');
    if (spaceIndex > 0 && spaceIndex < (int)raw.length() - 1) 
    {
      String text = raw.substring(spaceIndex + 1);
      text.trim();
      oled_l1 = text;
      oledRender();
      Serial.println("OK;OL1;");
    }
    else
    {
      Serial.println("ERR;BAD_OL1;");
    }
    return;
  }

  if (c.startsWith("OL2 ")) 
  {
    int spaceIndex = raw.indexOf(' ');
    if (spaceIndex > 0 && spaceIndex < (int)raw.length() - 1) {
      String text = raw.substring(spaceIndex + 1);
      text.trim();
      oled_l2 = text;
      oledRender();
      Serial.println("OK;OL2;");
    } else {
      Serial.println("ERR;BAD_OL2;");
    }
    return;
  }

  // --- Lệnh không nhận diện được ---
  Serial.print("ERR;UNKNOWN_CMD=");
  Serial.print(c);
  Serial.println(";");
}

// ===== Setup =====
void setup() 
{
  Serial.begin(115200);
  setupPins();

  // I2C (SDA, SCL theo pins.h)
  Wire.begin(SDA_PIN, SCL_PIN);

  // OLED
  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED)) 
  {   // OLED = 0x3C trong pins.h
    Serial.println("ERR;OLED_FAIL;");
    OLED_OK = false;
    beep(300);
  }
  else
  {
    OLED_OK = true;
    oled_l1 = "ESP32 KIT";
    oled_l2 = "READY";
    oledRender();
    Serial.println("OLED OK");
  }

  // ADS1115
  ADS_OK = ads.begin(ADS1115);    // ADS1115 = 0x48 trong pins.h
  if (!ADS_OK) {
    Serial.println("ERR;ADS_FAIL;");
  } else {
    ads.setGain(GAIN_ONE);        // +/-4.096V, 1 bit ~ 0.125mV
    Serial.println("ADS1115 OK");
  }

  // WS2812
  strip.begin();
  strip.show();        // tắt hết
  setRGB(0, 0, 0);     // OFF

  delay(300);
  Serial.println("ESP32 KIT READY");
}

// ===== Loop =====
void loop() 
{
  static String buffer;

  while (Serial.available()) 
  {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') 
    {
      if (buffer.length() > 0) 
      {
        handleCommand(buffer);
        buffer = "";
      }
    }
    else 
    {
      buffer += ch;
    }
  }

  delay(5);
}
