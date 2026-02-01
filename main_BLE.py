import machine
import time
import network
import ubluetooth
import esp32
from micropython import const

# --- KONFIGURACE HARDWARU ---
# VRx ---> GPIO 34
# VRy ---> GPIO 35
# SW  ---> GPIO 26
PIN_VRX = 34
PIN_VRY = 35
PIN_SW  = 26

# --- NASTAVENÍ CITLIVOSTI ---
# Rozsah ADC je 0-4095. Střed je cca 1800-2200.
# Pokud hodnota vybočí z těchto mezí, považujeme to za pohyb a ESP zůstane vzhůru.
WAKE_THRESHOLD_LOW = 1400  
WAKE_THRESHOLD_HIGH = 2600

# --- ČASOVAČE ---
IDLE_TIMEOUT = 15     # Za kolik sekund nečinnosti se usne (když jste připojeni)
SLEEP_INTERVAL = 300  # Jak často (v ms) se kontroluje pohyb ve spánku

# ==========================================
# 1. RYCHLÁ KONTROLA POHYBU (Před zapnutím rádia)
# ==========================================
# Tuto část provádíme hned po startu. Pokud se nic nehýbe, 
# jdeme hned spát, abychom neplýtvali energií na start Bluetooth.

# Nastavení ADC
adc_x = machine.ADC(machine.Pin(PIN_VRX))
adc_y = machine.ADC(machine.Pin(PIN_VRY))
adc_x.atten(machine.ADC.ATTN_11DB)
adc_y.atten(machine.ADC.ATTN_11DB)
btn_sw = machine.Pin(PIN_SW, machine.Pin.IN, machine.Pin.PULL_UP)

# Přečtení hodnot
x_val = adc_x.read()
y_val = adc_y.read()
btn_pressed = (btn_sw.value() == 0)

# Je joystick v pohybu?
is_moving = (x_val < WAKE_THRESHOLD_LOW or x_val > WAKE_THRESHOLD_HIGH or 
             y_val < WAKE_THRESHOLD_LOW or y_val > WAKE_THRESHOLD_HIGH or 
             btn_pressed)

# Zjistíme důvod startu (zda to byl Deep Sleep timer)
reset_cause = machine.reset_cause()

if reset_cause == machine.DEEPSLEEP_RESET:
    # Pokud jsme se vzbudili jen kvůli kontrole a NIC se nehýbe -> spíme dál
    if not is_moving:
        # Tady se rádio vůbec nezapne = obrovská úspora
        machine.deepsleep(SLEEP_INTERVAL)

# ==========================================
# 2. START SYSTÉMU (Pokud byl detekován pohyb)
# ==========================================

# Vypnutí Wi-Fi (obrovský žrout energie, nepotřebujeme ho)
wlan = network.WLAN(network.STA_IF)
wlan.active(False)
ap = network.WLAN(network.AP_IF)
ap.active(False)

# --- BLE DEFINICE ---
BLE_SERVICE_UUID = ubluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
BLE_CHAR_UUID    = ubluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")

class BLEJoystick:
    def __init__(self):
        self.ble = ubluetooth.BLE()
        self.ble.active(True)
        self.ble.irq(self.ble_irq)
        self.register()
        self.connected = False
        self.advertiser()

    def ble_irq(self, event, data):
        if event == 1: # _IRQ_CENTRAL_CONNECT
            self.connected = True
            print("RPi připojeno!")
        elif event == 2: # _IRQ_CENTRAL_DISCONNECT
            self.connected = False
            print("RPi odpojeno, znovu vysílám...")
            self.advertiser()

    def register(self):
        self.tx = (BLE_CHAR_UUID, ubluetooth.FLAG_READ | ubluetooth.FLAG_NOTIFY,)
        self.service = (BLE_SERVICE_UUID, (self.tx,),)
        ((self.tx_handle,),) = self.ble.gatts_register_services((self.service,))

    def send(self, data):
        if self.connected:
            try:
                self.ble.gatts_write(self.tx_handle, data)
                self.ble.gatts_notify(0, self.tx_handle)
            except:
                pass

    def advertiser(self):
        name = "ESP32-Joystick"
        adv_data = bytearray(b'\x02\x01\x06') + bytearray((len(name) + 1, 0x09)) + name.encode()
        self.ble.gap_advertise(100, adv_data)

# --- HLAVNÍ SMYČKA ---
ble = BLEJoystick()
last_cmd = "CENTER"
last_activity_time = time.time()

print("BLE Joystick aktivní! (Režim Wake-on-Move)")

while True:
    # Čtení aktuálního stavu
    x = adc_x.read()
    y = adc_y.read()
    btn = btn_sw.value() == 0
    
    # Logika příkazů (zachováno: Doleva = SELECT)
    cmd = "CENTER"
    if btn: 
        cmd = "SELECT"
    elif y < 1600: 
        cmd = "UP"
    elif y > 2200: 
        cmd = "DOWN"
    elif x < 1600: 
        cmd = "SELECT" # Doleva funguje jako potvrzení
    elif x > 2200: 
        cmd = "RIGHT"

    # Odeslání dat při změně
    if cmd != last_cmd:
        if cmd != "CENTER":
            print(f"Odesílám: {cmd}")
            ble.send(cmd)
            # Jakákoliv akce resetuje odpočet do spánku
            last_activity_time = time.time()
        last_cmd = cmd
        time.sleep_ms(200)

    # Pokud joystick není ve středu, resetujeme odpočet (aby neusnul při držení páčky)
    if cmd != "CENTER":
        last_activity_time = time.time()

    # KONTROLA NEČINNOSTI
    if (time.time() - last_activity_time) > IDLE_TIMEOUT:
        print("Nečinnost -> Vypínám rádio a jdu spát.")
        time.sleep_ms(100) # Čas na odeslání logu
        
        # Vypneme Bluetooth
        ble.ble.active(False)
        
        # Jdeme do Deep Sleep s budíkem za 300ms (pro kontrolu pohybu)
        machine.deepsleep(SLEEP_INTERVAL)

    time.sleep_ms(50)