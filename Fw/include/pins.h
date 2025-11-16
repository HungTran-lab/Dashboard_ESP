#ifndef PINS_H
#define PINS_H

// ==== I2C ====
#define SDA_PIN 19
#define SCL_PIN 21

// ==== PCF8574 ====
#define OLED     0X3C   // Relay outputs
#define ADS1115  0x48   // Digital inputs

// ==== Analog ====
#define ADC1 32
#define ADC2 33
#define ADC3 25

// ==== Input ====
#define SENSOR1 39
#define SENSOR2 36
#define SENSOR3 34
#define SENSOR4 35
#define SENSOR5 26  // NON BUTTON

// ==== Output ====
#define RELAY1 15
#define RELAY2 4
#define RELAY3 17
#define RELAY4 5

// ==== I/O SPARE ====
#define SIO1 16     //OK
#define SIO2 27     //OK
#define SIO3 14     //OK
//#define SIO4 13   --> Option

// ==== Buzzer + WS2812 ====
#define BUZZER_PIN 18
#define WS2812_PIN 13
#define NUMPIXELS 1

// ==== RS232 ====
#define RXD 23
#define TXD 22

// ==== HASS ====
//#define RXD0 DEFAULT
//#define TXD0 DEFAULT

// ==== SPARE ==== Gần như ko sử dụng
#define SPARE1 12
#define SPARE2 2     // Not yet use

#endif
