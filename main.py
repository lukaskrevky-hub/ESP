import machine
import time
import ubluetooth

PIN_VRX = 34
PIN_VRY = 35
PIN_SW  = 26

WAKE_LOW = 1200
WAKE_HIGH = 2600
SLEEP_INTERVAL = 300
IDLE_TIMEOUT = 15

# Inicializace hned na začátku pro maximální rychlost
adc_x = machine.ADC(machine.Pin(PIN_VRX))
adc_y = machine.ADC(machine.Pin(PIN_VRY))
adc_x.atten(machine.ADC.ATTN_11DB)
adc_y.atten(machine.ADC.ATTN_11DB)
btn_sw = machine.Pin(PIN_SW, machine.Pin.IN, machine.Pin.PULL_UP)

def get_direction():
    x = adc_x.read()
    y = adc_y.read()
    btn = (btn_sw.value() == 0)

    if btn: return "SELECT"
    if y < WAKE_LOW: return "UP"
    if y > WAKE_HIGH: return "DOWN"
    if x < WAKE_LOW: return "LEFT"
    if x > WAKE_HIGH: return "RIGHT"
    return "CENTER"

current_cmd = get_direction()

# Pokud není pohyb, okamžitě spíme (zpoždění 0)
if machine.reset_cause() == machine.DEEPSLEEP_RESET:
    if current_cmd == "CENTER":
        machine.deepsleep(SLEEP_INTERVAL)

print("Pohyb detekován, startuji bleskové BLE...")

BLE_SERVICE_UUID = ubluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
BLE_CHAR_UUID    = ubluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")

class BLEServer:
    def __init__(self):
        self.ble = ubluetooth.BLE()
        self.ble.active(True)
        self.ble.irq(self.ble_irq)
        self.register()
        self.connected = False
        self.advertise()

    def ble_irq(self, event, data):
        if event == 1:
            self.connected = True
            print("Připojeno k RPi!")
        elif event == 2:
            self.connected = False
            print("RPi odpojeno.")

    def register(self):
        self.tx = (BLE_CHAR_UUID, ubluetooth.FLAG_READ | ubluetooth.FLAG_NOTIFY,)
        self.service = (BLE_SERVICE_UUID, (self.tx,),)
        ((self.tx_handle,),) = self.ble.gatts_register_services((self.service,))

    def send(self, command):
        if self.connected:
            try:
                self.ble.gatts_write(self.tx_handle, command.encode())
                self.ble.gatts_notify(0, self.tx_handle)
                print(f"Odesláno: {command}")
            except Exception as e:
                print(f"Chyba odesílání: {e}")

    def advertise(self):
        name = "ESP-JOY"
        adv = bytearray(b'\x02\x01\x06') + bytearray((len(name)+1, 0x09)) + name.encode()
        # EXTRÉMNÍ ZRYCHLENÍ: 20000 mikrosekund (20 ms)
        # To zajistí, že si nás malina všimne prakticky okamžitě
        self.ble.gap_advertise(20000, adv) 

    def stop(self):
        self.ble.active(False)

ble = BLEServer()

last_cmd = "CENTER"
last_activity_time = time.time()

# Zůstáváme v hybridní smyčce 15 vteřin pro okamžité reakce
while True:
    cmd = get_direction()
    if cmd != "CENTER":
        last_activity_time = time.time()
        
    if cmd != last_cmd:
        if cmd != "CENTER":
            retries = 0
            while not ble.connected and retries < 30:
                time.sleep_ms(50) # Zrychlený polling stavu
                retries += 1
            ble.send(cmd)
        last_cmd = cmd
        time.sleep_ms(150)

    if (time.time() - last_activity_time) > IDLE_TIMEOUT:
        print("Timeout, jdu do Deep Sleepu...")
        ble.stop()
        time.sleep_ms(50)
        machine.deepsleep(SLEEP_INTERVAL)
        
    time.sleep_ms(20)
