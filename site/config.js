/* Config del frontend, aparte de app.js para que se pueda editar sin tocar
   lógica. Único valor por ahora: la API key de Google Maps Embed API, usada
   por mapEmbedHtml() en app.js para el mapa del panel de detalle.

   Cómo conseguirla:
   1. https://console.cloud.google.com/ -> crear (o reusar) un proyecto.
   2. Activar "Maps Embed API" en ese proyecto.
   3. Activar una cuenta de facturación en el proyecto (obligatorio para que
      la key funcione, aunque el uso de esta API puntual es gratis e
      ilimitado -- no vas a recibir cobros por esto).
   4. Credenciales -> Crear credenciales -> Clave de API.
   5. IMPORTANTE -- restringir la key: en "Restricciones de la aplicación"
      elegir "Referencias HTTP (sitios web)" y agregar el dominio real del
      sitio (ej. tudominio.com/*). Esta key queda visible en el HTML del
      sitio (es normal, así funcionan las keys de Maps del lado del
      navegador) -- la restricción por dominio es lo que evita que otro la
      use desde otro sitio, no el hecho de que esté oculta.
   6. Pegar la key acá abajo, reemplazando YOUR_KEY_HERE.

   Si se deja YOUR_KEY_HERE (o se borra el archivo), el sitio simplemente no
   muestra el mapa -- no rompe nada más. */
window.GOOGLE_MAPS_EMBED_KEY = "YOUR_KEY_HERE";
