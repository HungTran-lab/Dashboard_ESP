#ifndef CONFIG_H
#define CONFIG_H

// Serial
#define SERIAL_BAUD     115200

// Delay time
#define RELAY_DELAY_MS  200
#define LOOP_DELAY_MS   2000

// Debug mode
#define DEBUG true   // đặt false nếu không muốn in Serial

#if DEBUG
  #define DBG_PRINT(x)  Serial.print(x)
  #define DBG_PRINTLN(x) Serial.println(x)
#else
  #define DBG_PRINT(x)
  #define DBG_PRINTLN(x)
#endif

#endif
