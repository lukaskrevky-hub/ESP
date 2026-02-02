import machine
import time
import network
import ubluetooth
import esp32
from micropython import const

# --- KONFIGURACE HARDWARU ---
PIN_VRX = 34
PIN_VRY = 35
PIN_SW  = 26

# Citlivost probuzení
WAKE_LOW = 1300
WAKE_HIGH = 2700

# Časovače
IDLE_TIMEOUT = 15     # Sekundy do usnutí
SLEEP_INTERVAL = 300  # Pulsed polling ve spánku

# 1. KONTROLA POHYBU (Blesková kontrola před startem rádia)
adc_x = machine.ADC(machine.Pin(PIN_VRX))
adc_y = machine.ADC(machine.Pin(PIN_VRY))
adc_x.atten(machine.ADC.ATTN_11DB)
adc_y.atten(machine.ADC.ATTN_11DB)
btn_sw = machine.Pin(PIN_SW, machine.Pin.IN, machine.Pin.PULL_UP)

x_val = adc_x.read()
y_val = adc_y.read()
btn = (btn_sw.value() == 0)

is_moving = (x_val < WAKE_LOW or x_val > WAKE_HIGH or 
             y_val < WAKE_LOW or y_val > WAKE_HIGH or btn)

# Pokud jsme se vzbudili z časovače a nic se nehýbe -> spíme dál
if machine.reset_cause() == machine.DEEPSLEEP_RESET:
    if not is_moving:
        machine.deepsleep(SLEEP_INTERVAL)

# 2. START (Jen pokud je pohyb)
wlan = network.WLAN(network.STA_IF); wlan.active(False)
ap = network.WLAN(network.AP_IF); ap.active(False)

BLE_SERVICE_UUID = ubluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
BLE_CHAR_UUID    = ubluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")

class BLEJoystick:
    def __init__(self):
        self.ble = ubluetooth.BLE()
        self.ble.active(True)
        self.ble.irq(self.ble_irq)
        self.register()
        self.connected = False
        self.shutting_down = False
        self.advertise()

    def ble_irq(self, event, data):
        if event == 1: 
            self.connected = True
            print(">>> PRIPOJENO <<<") # Vypíše se jen jednou
        elif event == 2: # Odpojeno
            self.connected = False
            print(">>> ODPOJENO <<<") # Vypíše se jen jednou
            if not self.shutting_down:
                self.advertise()

    def register(self):
        self.tx = (BLE_CHAR_UUID, ubluetooth.FLAG_READ | ubluetooth.FLAG_NOTIFY,)
        self.service = (BLE_SERVICE_UUID, (self.tx,),)
        ((self.tx_handle,),) = self.ble.gatts_register_services((self.service,))

    def send(self, data):
        if self.connected:
            try:
                self.ble.gatts_write(self.tx_handle, data)
                self.ble.gatts_notify(0, self.tx_handle)
            except: pass

    def advertise(self):
        name = "ESP-JOY"
        adv = bytearray(b'\x02\x01\x06') + bytearray((len(name)+1, 0x09)) + name.encode()
        self.ble.gap_advertise(30000, adv)

    def stop(self):
        self.shutting_down = True
        self.ble.active(False)

ble = BLEJoystick()
last_cmd = "CENTER"
last_time = time.time()
last_print_time = time.ticks_ms()

# print("BLE Joystick Start") 

while True:
    x = adc_x.read(); y = adc_y.read(); btn = (btn_sw.value() == 0)
    
    cmd = "CENTER"
    if btn: cmd = "SELECT"
    elif y < 1500: cmd = "UP"
    elif y > 2400: cmd = "DOWN"
    elif x < 1500: cmd = "SELECT"
    elif x > 2400: cmd = "RIGHT"

    # --- VÝPIS SOUŘADNIC (Každých 500 ms) ---
    if time.ticks_diff(time.ticks_ms(), last_print_time) > 500:
        print(f"X: {x} | Y: {y} | Směr: {cmd}")
        last_print_time = time.ticks_ms()

    if cmd != last_cmd:
        if cmd != "CENTER":
            ble.send(cmd)
            last_time = time.time()
        last_cmd = cmd
        time.sleep_ms(150)

    if cmd != "CENTER": last_time = time.time()

    # Pokud jsme nečinní, usneme
    if (time.time() - last_time) > IDLE_TIMEOUT:
        print(">>> NEAKTIVITA - USINAM <<<")
        ble.stop() # Bezpečné vypnutí rádia
        time.sleep_ms(50) # Krátká pauza
        machine.deepsleep(SLEEP_INTERVAL)


    time.sleep_ms(20)
