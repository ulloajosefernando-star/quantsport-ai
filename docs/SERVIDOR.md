# Programador en un servidor (sin depender de tu Mac)

En el servidor solo corre el programador. El panel no queda abierto a internet: si quieres verlo, usas un túnel SSH desde tu Mac. Nada de esto apuesta, deposita ni inicia sesión en casas de apuestas.

## 1. Crear el servidor (lo haces tú)

Elige una opción. Precios revisados el 24/10/2026.

- **Oracle Cloud Always Free (0 MXN).** Crea la cuenta en oracle.com/cloud/free. Pide tarjeta para verificar identidad, pero no cobra mientras uses recursos Always Free. Crea una instancia **VM.Standard.A1.Flex** con 1–2 OCPU y 6–12 GB, imagen **Ubuntu 24.04**, y sube tu llave SSH pública (`~/.ssh/id_ed25519.pub`). Desde junio de 2026 el límite gratuito es de 2 OCPU y 12 GB en total. Si sale "Out of capacity", prueba otra zona o inténtalo más tarde. Oracle puede recortar o reclamar recursos gratuitos.
- **Hetzner (≈6.49 € al mes, unos 130 MXN).** Servidor CPX02 o el más barato disponible, con imagen Ubuntu 24.04 y tu llave SSH.

No hace falta abrir puertos aparte de SSH (22).

## 2. Instalar

Desde tu Mac:

```bash
ssh ubuntu@IP_DEL_SERVIDOR          # en Hetzner el usuario es root
git clone https://github.com/ulloajosefernando-star/quantsport-ai.git
cd quantsport-ai
./scripts/instalar_servidor.sh       # la primera vez crea .env y se detiene
nano .env                            # DATA_MODE=real, SCHEDULER_ENABLED=true,
                                     # THE_ODDS_API_KEY=..., THE_ODDS_API_REGIONS=eu
./scripts/instalar_servidor.sh       # instala y deja el servicio corriendo
```

Como el repositorio es privado, `git clone` te pedirá usuario y un *token* de GitHub (Settings → Developer settings → Fine-grained tokens, solo lectura de este repo). No uses tu contraseña.

El servicio arranca solo cuando el servidor se reinicia. Si falla, systemd lo reinicia a los 30 s.

## 3. Llevar tu historial (opcional)

Para conservar los pronósticos y el CLV que ya juntó tu Mac, copia la base antes de arrancar el servicio en el servidor:

```bash
scp ~/quantsport-ai/data/quantsport.db ubuntu@IP_DEL_SERVIDOR:quantsport-ai/data/
```

Si no la copias, la tarea diaria vuelve a descargar los CSV y empieza el historial de pronósticos desde cero.

## 4. Apagar el de tu Mac

Para no gastar créditos dos veces con la misma clave, deja un solo programador:

```bash
kill $(cat ~/quantsport-ai/programador.pid)
```

## Uso diario

```bash
journalctl -u quantsport-programador -f             # registro en vivo
sudo systemctl restart quantsport-programador       # reiniciar
git pull && ./scripts/instalar_servidor.sh          # actualizar a la última versión
```

Para ver el panel desde tu Mac sin exponerlo:

```bash
ssh -L 8000:localhost:8000 ubuntu@IP_DEL_SERVIDOR
cd quantsport-ai/backend && SCHEDULER_ENABLED=false .venv/bin/uvicorn app.main:app --port 8000 --host 127.0.0.1
```

`SCHEDULER_ENABLED=false` evita que el panel arranque un segundo programador. Luego, en tu Mac, corre el frontend (`cd frontend && npm run dev`) y abre http://localhost:5173.
