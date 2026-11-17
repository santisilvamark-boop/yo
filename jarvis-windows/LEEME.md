# Jarvis para Windows

Asistente de voz que se queda abierto en tu computadora. Dices **"Hey Jarvis"**, le pides algo y te responde con voz. También puede usar tu computadora por ti: abrir programas, escribir correos, usar Teams, el navegador, etc.

## Instalación (una sola vez)

1. Descarga esta carpeta `jarvis-windows` a tu computadora (por ejemplo en `Documentos\jarvis-windows`).
2. Consigue una **clave de API de Anthropic**: entra en platform.claude.com, ve a **API Keys** y crea una. Hay que añadir saldo para usarla.
3. Haz doble clic en **`Instalar.bat`**. El instalador:
   - instala Python si no lo tienes,
   - instala todo lo necesario (tarda unos minutos),
   - te pide la clave y tu nombre,
   - descarga los modelos de voz,
   - crea un acceso directo **Jarvis** en el escritorio.

## Uso

- Abre **Jarvis** desde el escritorio. Aparece una ventanita que siempre queda encima.
- Di **"Hey Jarvis"** (en inglés, así se activa), espera el pitido y di lo que quieras en español.
  - "Abre Outlook y escribe un correo a Juan diciéndole que la reunión se mueve al jueves".
  - "Busca en Teams el último mensaje de María y léemelo".
  - "Abre YouTube y pon música tranquila".
- También puedes pulsar **Ctrl+Alt+J** o el botón 🎤, o escribir en el cuadro de abajo.
- **Para detenerlo:** botón **Detener**, **Ctrl+Alt+S**, o lleva el ratón a la **esquina superior izquierda** de la pantalla.

## Seguridad

- Antes de **enviar** un correo o mensaje, borrar o comprar algo, Jarvis te pregunta en voz alta y espera tu "sí".
- Antes de ejecutar un comando de PowerShell te aparece una ventana para permitirlo o no. Se puede desactivar en `config.json` (`"confirmar_comandos": false`), pero no es recomendable.
- Mientras Jarvis trabaja, no muevas el ratón ni escribas: está usando tu computadora.
- Las capturas de pantalla se envían a Claude para que pueda ver lo que hace. No le pidas tareas con información que no quieras compartir.
- No automatiza juegos en línea: suele estar prohibido y pueden banearte la cuenta.

## Ajustes (`config.json`, se crea al primer arranque)

| Ajuste | Para qué sirve |
|---|---|
| `esfuerzo` | `low` (rápido, por defecto), `medium` o `high` (piensa más en tareas difíciles). |
| `modo_rapido` | `true` = respuestas más rápidas, pero cuesta el doble. |
| `voz` | Voz de Microsoft, por ejemplo `es-MX-DaliaNeural`, `es-MX-JorgeNeural`, `es-ES-ElviraNeural`, `es-CO-SalomeNeural`. |
| `modelo_whisper` | `small` por defecto. `base` es más rápido y menos preciso; `medium` es más preciso y más lento. |
| `sensibilidad_activacion` | 0.5 por defecto. Si se activa sola, súbela a 0.7; si no te oye, bájala a 0.3. |
| `ancho_captura` | Tamaño de las capturas que ve Claude. Más pequeño es más rápido y barato. |

## Coste aproximado

Usa el modelo Claude Opus 5.5 ($4 por millón de tokens de entrada y $20 por millón de salida). Una pregunta simple cuesta alrededor de un centavo. Una tarea en la computadora suele costar entre 5 y 50 centavos, según cuántos pasos y capturas necesite. Puedes ver el gasto real en platform.claude.com, en Usage, y poner un límite mensual.

## Si algo falla

Abre **`Jarvis (con consola).bat`**: verás los mensajes de error en una ventana negra.
