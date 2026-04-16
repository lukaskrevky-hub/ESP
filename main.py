"""
FIRMWARE KLIENTSKÉ JEDNOTKY (ESP32) - ASISTENČNÍ HUB
----------------------------------------------------
Tento skript běží na mikrokontroléru ESP32. Obsluhuje analogový joystick,
využívá režim hlubokého spánku (Deep Sleep) pro maximalizaci výdrže baterie
a komunikuje s centrální bránou pomocí Bluetooth Low Energy (BLE).
Zároveň obsahuje funkci pro OTA (Over-The-Air) aktualizaci kódu přes Wi-Fi.
"""

# ==========================================
# 1. IMPORT POTŘEBNÝCH KNIHOVEN
# ==========================================
import machine                   # Přístup k nízkoúrovňovému hardwaru (piny, ADC převodníky, Deep Sleep)
import time                      # Časové funkce pro prodlevy a měření doby nečinnosti
import network                   # Ovládání síťových rozhraní (slouží pro OTA aktualizaci přes Wi-Fi)
import ubluetooth                # Nativní MicroPython knihovna pro Bluetooth Low Energy (BLE)
import esp32                     # Specifické funkce dostupné pouze pro čipy architektury ESP32
import urequests                 # Knihovna pro odesílání HTTP požadavků (stažení nového kódu)
from micropython import const    # Konstanty pro optimalizaci využití paměti RAM

# ==========================================
# 2. KONFIGURACE SÍTĚ A AKTUALIZACÍ (OTA)
# ==========================================
# Tyto údaje slouží pro jednorázové připojení k internetu při požadavku na aktualizaci
WIFI_SSID = "DOPLNIT_WIFI_JMENO"
WIFI_PASS = "DOPLNIT_WIFI_HESLO"
OTA_URL   = "http://DOPLNIT_URL_OD_VEDOUCIHO_KDE_JE_SOUBOR/main.py"

# ==========================================
# 3. KONFIGURACE HARDWARU (PINY A PRAHY)
# ==========================================
# Definice GPIO pinů, ke kterým je joystick fyzicky připojen
PIN_VRX = 34    # Analogový pin pro osu X
PIN_VRY = 35    # Analogový pin pro osu Y
PIN_SW  = 26    # Digitální pin pro stisk tlačítka joysticku

# Prahové hodnoty pro detekci pohybu joysticku (ADC vrací 0 až 4095)
# Hodnoty byly upraveny pro pacienty s horší motorikou (tzv. větší mrtvá zóna).
# Aby systém zareagoval, musí být páčka vychýlena téměř do krajních poloh.
WAKE_LOW = 200
WAKE_HIGH = 3800

# Parametry pro řízení spotřeby energie
IDLE_TIMEOUT = 30       # Čas (v sekundách), po kterém přejde zařízení do spánku při nečinnosti
SLEEP_INTERVAL = 300    # Doba spánku (v milisekundách) v režimu Pulsed Polling

# ==========================================
# 4. INICIALIZACE PERIFERIÍ
# ==========================================
# Nastavení A/D převodníků pro čtení os
adc_x = machine.ADC(machine.Pin(PIN_VRX))
adc_y = machine.ADC(machine.Pin(PIN_VRY))

# ATTN_11DB nastavuje rozsah útlumu na 3.3V (standardní napětí logiky ESP32)
adc_x.atten(machine.ADC.ATTN_11DB)
adc_y.atten(machine.ADC.ATTN_11DB)

# Nastavení pinu tlačítka s vnitřním pull-up rezistorem
# V klidu je na pinu log. 1, při stisku log. 0 (spojeno se zemí)
btn_sw = machine.Pin(PIN_SW, machine.Pin.IN, machine.Pin.PULL_UP)

def read_inputs():
    """Přečte a vrátí aktuální hodnoty z os X, Y a stav tlačítka."""
    return adc_x.read(), adc_y.read(), (btn_sw.value() == 0)

# ==========================================
# 5. OCHRANA PŘED FALEŠNÝM PROBUZENÍM
# ==========================================
# Okamžitě po zapnutí zjistíme, zda se s joystickem hýbe
x, y, btn = read_inputs()
is_moving = (x < WAKE_LOW or x > WAKE_HIGH or y < WAKE_LOW or y > WAKE_HIGH or btn)

# Pokud bylo zařízení probuzeno časovačem z Deep Sleepu, ale joystick je v klidu,
# znamená to "pulsní kontrolu". Zařízení ihned znovu uspíme pro úsporu baterie.
if machine.reset_cause() == machine.DEEPSLEEP_RESET:
    if not is_moving:
        machine.deepsleep(SLEEP_INTERVAL)

# Standardně držíme oba Wi-Fi moduly vypnuté (žerou nejvíce energie)
wlan = network.WLAN(network.STA_IF); wlan.active(False)
ap = network.WLAN(network.AP_IF); ap.active(False)

# ==========================================
# 6. DEFINICE BLUETOOTH (BLE) UUID
# ==========================================
# Unikátní identifikátory služeb a charakteristik. Shodují se s přijímačem na Raspberry Pi.
BLE_SERVICE_UUID = ubluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E") # Hlavní služba
BLE_TX_CHAR_UUID = ubluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E") # Odesílání povelů z ESP32 (TX)
BLE_RX_CHAR_UUID = ubluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E") # Příjem zpráv do ESP32 (RX)

# ==========================================
# 7. FUNKCE PRO VZDÁLENOU AKTUALIZACI (OTA)
# ==========================================
def perform_ota():
    """
    Zapne Wi-Fi, stáhne nový zdrojový kód ze zadané URL adresy,
    přepíše vlastní soubor main.py a fyzicky restartuje mikrokontrolér.
    """
    print("Zahajuji OTA Aktualizaci! Zapínám WiFi...")
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    
    # Ochranný časovač - maximálně 15 vteřin pro připojení
    timeout = 15
    while not wlan.isconnected() and timeout > 0:
        time.sleep(1)
        timeout -= 1
        
    if wlan.isconnected():
        print("WiFi připojeno. Stahuji nový main.py...")
        try:
            # HTTP GET požadavek na stažení nového kódu z public serveru
            response = urequests.get(OTA_URL)
            if response.status_code == 200:
                # Přepsání lokálního souboru novým kódem
                with open("main.py", "w") as f:
                    f.write(response.text)
                print("Aktualizace úspěšná! Restartuji ESP32...")
                time.sleep(1)
                machine.reset() # Tvrdý restart pro zavedení nového kódu
            else:
                print("Chyba stahování (Status:", response.status_code, ")")
        except Exception as e:
            print("Chyba OTA:", e)
    else:
        print("Nelze se připojit k WiFi. Aktualizace zrušena.")
    
    # Pokud aktualizace selže, Wi-Fi se pro šetření baterie opět vypne
    wlan.active(False)

# ==========================================
# 8. TŘÍDA PRO OBSLUHU BLUETOOTH
# ==========================================
class BLEJoystick:
    """Třída zapouzdřující konfiguraci a komunikaci přes Bluetooth Low Energy."""
    def __init__(self):
        self.ble = ubluetooth.BLE()    
        self.ble.active(True)          
        self.ble.irq(self.ble_irq)     # Namapování funkce na asynchronní události (přerušení)
        self.register()                
        self.connected = False         
        self.shutting_down = False     
        self.conn_handle = None        
        self.advertise()               

    def ble_irq(self, event, data):
        """Callback funkce volaná při události na Bluetooth adaptéru."""
        global last_time
        if event == 1: 
            # UDÁLOST: Zařízení připojeno
            self.conn_handle, _, _ = data
            self.connected = True
            print(">>> PŘIPOJENO k Raspberry Pi <<<")
            last_time = time.time()
        elif event == 2: 
            # UDÁLOST: Zařízení odpojeno
            self.conn_handle = None
            self.connected = False
            print(">>> ODPOJENO <<<")
            # Pokud se nevypínáme záměrně, začneme se znovu vysílat (advertisovat)
            if not self.shutting_down:
                self.advertise()
        elif event == 3: 
            # UDÁLOST (_IRQ_GATTS_WRITE): Raspberry Pi zapsalo data do naší charakteristiky (Příjem)
            conn_handle, value_handle = data
            if value_handle == self.rx_handle:
                # Přečteme zprávu a dekódujeme ji
                msg = self.ble.gatts_read(self.rx_handle).decode('utf-8').strip()
                # Pokud obdržíme tajný signál OTA_START, ihned zahájíme OTA proces
                if msg == "OTA_START":
                    perform_ota()

    def register(self):
        """Zaregistruje GATT služby a přístupová práva (Read, Write, Notify)."""
        self.tx = (BLE_TX_CHAR_UUID, ubluetooth.FLAG_READ | ubluetooth.FLAG_NOTIFY,)
        self.rx = (BLE_RX_CHAR_UUID, ubluetooth.FLAG_WRITE,)
        self.service = (BLE_SERVICE_UUID, (self.tx, self.rx,),)
        ((self.tx_handle, self.rx_handle),) = self.ble.gatts_register_services((self.service,))

    def send(self, data):
        """Odešle data (směr pohybu) centrální bráně přes Notify mechanismus."""
        if self.connected and self.conn_handle is not None:
            try:
                self.ble.gatts_write(self.tx_handle, data.encode())
                self.ble.gatts_notify(self.conn_handle, self.tx_handle)
                return True
            except: 
                return False
        return False

    def advertise(self):
        """Začne do okolí vysílat inzertní pakety se jménem zařízení."""
        name = "ESP-JOY"
        adv = bytearray(b'\x02\x01\x06') + bytearray((len(name)+1, 0x09)) + name.encode()
        self.ble.gap_advertise(30000, adv)

    def stop(self):
        """Bezpečně odpojí klienty a vypne Bluetooth modul."""
        self.shutting_down = True
        if self.connected and self.conn_handle is not None:
            try: self.ble.gap_disconnect(self.conn_handle)
            except: pass
            time.sleep_ms(100) 
        self.ble.active(False) 

# ==========================================
# 9. HLAVNÍ ŘÍDICÍ SMYČKA
# ==========================================
# Inicializace proměnných
ble = BLEJoystick()
last_cmd = "CENTER"     
last_time = time.time() 

print("Joystick úspěšně nastartován, čekám na spojení...")

try:
    while True:
        # Přečtení aktuálního stavu senzorů
        x, y, btn = read_inputs()

        # Vyhodnocení konkrétního směru podle přednastavených mezí z globální proměnné
        cmd = "CENTER"
        if btn: cmd = "SELECT"
        elif y < WAKE_LOW: cmd = "UP"
        elif y > WAKE_HIGH: cmd = "DOWN"
        elif x < WAKE_LOW: cmd = "LEFT"   
        elif x > WAKE_HIGH: cmd = "RIGHT"

        # Kontrola, zda uživatel aktuálně interaguje
        user_active = (cmd != "CENTER") or btn
        if user_active:
            last_time = time.time() # Resetování časovače nečinnosti

        # Odeslání dat přes Bluetooth POUZE při změně polohy (šetří baterii a zabraňuje floodingu sítě)
        if cmd != last_cmd:
            last_cmd = cmd
            if ble.connected: 
                ble.send(cmd)        
                time.sleep_ms(100) # Ochranná prodleva pro spolehlivé odeslání paketu   

        # Vyhodnocení nečinnosti (Idle Timeout)
        if (time.time() - last_time) > IDLE_TIMEOUT:
            print("Neaktivita, přecházím do režimu spánku...")
            ble.stop() # Ukončení Bluetooth relace
            time.sleep_ms(50)
            machine.deepsleep(SLEEP_INTERVAL) # Přechod do hlubokého spánku

        # Krátká smyčková prodleva pro ochranu CPU
        time.sleep_ms(20)

except KeyboardInterrupt:
    # Pro účely ladění (při stisku Ctrl+C v terminálu)
    print("\n--- PROGRAM PŘERUŠEN UŽIVATELEM ---")
    ble.stop()
    print("Bluetooth vypnuto.")
