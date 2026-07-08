# SniperBot Windows Portable

## Opcion con Python instalado

1. Instala Python 3.12 desde `python.org`.
2. Extrae el zip del proyecto.
3. Ejecuta `packaging\windows\start_bot.bat`.
4. Abre `http://127.0.0.1:8000`.

## Opcion Release Portable

1. Descarga `SniperBot-Windows-Portable.zip` desde GitHub Releases.
2. Extrae la carpeta.
3. Ejecuta `SniperBot.exe`.
4. En el primer arranque, el wizard crea `%APPDATA%\SniperBot\.env`.

La version portable arranca siempre en PAPER:

```env
PAPER_MODE=true
ALLOW_REAL_TRADING=false
```

REAL trading no se activa desde el wizard.
