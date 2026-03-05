# Import potřebných knihoven pro běh systému
import machine                   # Přístup k hardwaru (piny, ADC převodníky, hluboký spánek)
import time                      # Funkce pro práci s časem (časovače, zpoždění)
import network                   # Ovládání síťových rozhraní (WiFi)
import ubluetooth                # Knihovna pro práci s Bluetooth Low Energy (BLE)
import esp32                     # Specifické funkce pro čip ESP32
from micropython import const    # Pro definici konstant optimalizujících paměť

# --- KONFIGURACE HARDWARU ---
# Definice pinů, ke kterým je fyzicky připojen joystick
PIN_VRX = 34    # Analogový pin pro osu X (vodorovný pohyb)
PIN_VRY = 35    # Analogový pin pro osu Y (svislý pohyb)
PIN_SW  = 26    # Digitální pin pro tlačítko joysticku (stisknutí páčky)

# --- Nastavení citlivosti ---
# Běžná středová hodnota joysticku je kolem 1800-2000.
# Zde definujeme meze, po jejichž překročení se detekuje pohyb.
WAKE_LOW = 1300
WAKE_HIGH = 2700

# --- Časovače pro řízení spotřeby (Power Management) ---
IDLE_TIMEOUT = 30     # Doba nečinnosti v sekundách, po které se ESP32 uspí
SLEEP_INTERVAL = 300  # Záchranný interval probuzení (jak dlouho spí, pokud ho neprobudí páčka)  

# Inicializace analogových vstupů (ADC) pro čtení pozice
adc_x = machine.ADC(machine.Pin(PIN_VRX))
adc_y = machine.ADC(machine.Pin(PIN_VRY))
# ATTN_11DB znamená útlum, který umožňuje měřit napětí v plném rozsahu 0 až 3.3V
adc_x.atten(machine.ADC.ATTN_11DB)
adc_y.atten(machine.ADC.ATTN_11DB)
# Inicializace tlačítka s vnitřním Pull-Up rezistorem (drží hodnotu vysoko, stisk ji srazí na nulu)
btn_sw = machine.Pin(PIN_SW, machine.Pin.IN, machine.Pin.PULL_UP)

# Funkce, která přečte aktuální stav všech tří os joysticku
def read_inputs():
    # Vrací: hodnotu X, hodnotu Y, a True/False podle toho, zda je stisknuto tlačítko
    return adc_x.read(), adc_y.read(), (btn_sw.value() == 0)

# --- OCHRANA PŘED FALEŠNÝM PROBUZENÍM ---
# Ihned po startu zkontrolujeme aktuální polohu
x, y, btn = read_inputs()
# Zjistíme, jestli se páčka skutečně nachází mimo středovou zónu (nebo je stisknutá)
is_moving = (x < WAKE_LOW or x > WAKE_HIGH or y < WAKE_LOW or y > WAKE_HIGH or btn)

# machine.reset_cause() zjišťuje, jak se deska zapnula. 
# Pokud se zapnula probuzením z hlubokého spánku (DEEPSLEEP_RESET)...
if machine.reset_cause() == machine.DEEPSLEEP_RESET:
    # ... a joystick se přitom fyzicky nehýbe (byl to elektrický šum nebo falešný kontakt)
    if not is_moving:
        # Okamžitě desku zase uspíme, abychom neplýtvali baterii zbytečným startem Bluetooth
        machine.deepsleep(SLEEP_INTERVAL)

# --- OPTIMALIZACE SPOTŘEBY ---
# Vypnutí WiFi modulů. WiFi spotřebovává obrovské množství energie. 
# Protože komunikujeme přes Bluetooth, WiFi ihned po startu natvrdo vypínáme.
wlan = network.WLAN(network.STA_IF); wlan.active(False)
ap = network.WLAN(network.AP_IF); ap.active(False)

# --- BLUETOOTH LOW ENERGY (BLE) NASTAVENÍ ---
# Unikátní identifikátory (UUID) pro naši službu, přes kterou budeme posílat data
BLE_SERVICE_UUID = ubluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
BLE_CHAR_UUID    = ubluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E")

# Třída spravující veškerou Bluetooth komunikaci
class BLEJoystick:
    def __init__(self):
        self.ble = ubluetooth.BLE()    # Vytvoření instance BLE modulu
        self.ble.active(True)          # Zapnutí Bluetooth antény
        self.ble.irq(self.ble_irq)     # Nastavení funkce, která se zavolá při události (připojení/odpojení)
        self.register()                # Zaregistrování našich UUID do systému
        self.connected = False         # Stav připojení
        self.shutting_down = False     # Pojistka pro bezpečné vypnutí
        self.conn_handle = None        # ID aktuálního spojení
        self.advertise()               # Začneme vysílat do okolí, že jsme k dispozici

    # Tzv. Interrupt Handler - volá se automaticky, když přijde zpráva od Bluetooth čipu
    def ble_irq(self, event, data):
        global last_time
        if event == 1: # Událost 1: Zařízení (Raspberry) se úspěšně připojilo
            self.conn_handle, _, _ = data
            self.connected = True
            print(">>> PRIPOJENO k Raspberry Pi <<<")
            # KLÍČOVÉ: Po připojení resetujeme časovač nečinnosti, abychom odpočítávali dobu do usnutí až po připojení
            last_time = time.time()
        elif event == 2: # Událost 2: Zařízení se odpojilo
            self.conn_handle = None
            self.connected = False
            print(">>> ODPOJENO <<<")
            # Pokud zrovna záměrně neusínáme, začneme znovu vysílat (hledat Raspberry)
            if not self.shutting_down:
                self.advertise()

    # Registrace služby a charakteristiky do tzv. GATT tabulky
    def register(self):
        # Definuje charakteristiku jako "READ" (možno číst) a "NOTIFY" (odesílá upozornění o změně)
        self.tx = (BLE_CHAR_UUID, ubluetooth.FLAG_READ | ubluetooth.FLAG_NOTIFY,)
        self.service = (BLE_SERVICE_UUID, (self.tx,),)
        ((self.tx_handle,),) = self.ble.gatts_register_services((self.service,))

    # Odeslání dat přes Bluetooth na Raspberry Pi
    def send(self, data):
        if self.connected and self.conn_handle is not None:
            try:
                # Zapíše data do paměti a upozorní připojené zařízení (Raspberry Pi)
                self.ble.gatts_write(self.tx_handle, data.encode())
                # Důležité zapsat skutečné ID spojení, což zabrání crashi!
                self.ble.gatts_notify(self.conn_handle, self.tx_handle)
                return True
            except: 
                return False
        return False

    # Vysílání (Advertising) - ESP32 křičí do okolí "Tady jsem, jmenuji se ESP-JOY"
    def advertise(self):
        name = "ESP-JOY"
        # Sestavení standardizovaného BLE bytu s názvem
        adv = bytearray(b'\x02\x01\x06') + bytearray((len(name)+1, 0x09)) + name.encode()
        self.ble.gap_advertise(30000, adv)

    # Bezpečné odpojení a vypnutí před usnutím
    def stop(self):
        self.shutting_down = True
        if self.connected and self.conn_handle is not None:
            try: self.ble.gap_disconnect(self.conn_handle)
            except: pass
            time.sleep_ms(100) # Počkáme na dokončení odpojení
        self.ble.active(False) # Úplně vypneme Bluetooth anténu (šetří baterii)

# Inicializace Bluetooth třídy
ble = BLEJoystick()
last_cmd = "CENTER"     # Předchozí stav joysticku (zabraňuje spamování stejného povelu)
last_time = time.time() # Čas poslední aktivity uživatele

print("Joystick úspěšně nastartován, čekám na spojení...")

# --- HLAVNÍ SMYČKA PROGRAMU ---
try:
    while True:
        # 1. Přečtení fyzického stavu páčky a tlačítka
        x, y, btn = read_inputs()

        # 2. Vyhodnocení směru na základě analogových hodnot
        cmd = "CENTER"
        if btn: cmd = "SELECT"
        elif y < 1500: cmd = "UP"
        elif y > 2400: cmd = "DOWN"
        elif x < 1500: cmd = "LEFT"   
        elif x > 2400: cmd = "RIGHT"

        # 3. Zaznamenání aktivity (resetování časovače usínání)
        user_active = (cmd != "CENTER") or btn
        if user_active:
            last_time = time.time() # Detekován pohyb páčky, vynulujeme odpočet

        # 4. Odeslání povelu (pokud došlo ke změně, abychom zbytečně nespamovali)
        if cmd != last_cmd:
            last_cmd = cmd
            if ble.connected: 
                ble.send(cmd)        # Odeslání dat přes BLE
                time.sleep_ms(100)   # Krátká pauza pro stabilizaci po odeslání

        # 5. Kontrola nečinnosti (Power Management)
        # Pokud aktuální čas mínus čas poslední aktivity přesáhne nastavený limit (30 vteřin)
        if (time.time() - last_time) > IDLE_TIMEOUT:
            print("Neaktivita, přecházím do režimu spánku...")
            ble.stop()
            time.sleep_ms(50)
            machine.deepsleep(SLEEP_INTERVAL)

        # 6. Krátká pauza v cyklu odlehčí procesoru
        time.sleep_ms(20)

except KeyboardInterrupt:
    # Tento blok se vykoná, pokud program ručně zastavíme
    print("\n--- PROGRAM PŘERUŠEN UŽIVATELEM ---")
    ble.stop()
    print("Bluetooth vypnuto.")


