/* =====================================================================================================
   SMG · Shell compartido de las páginas internas (sidebar + topbar + utilidades comunes).
   Uso en cada página (en el <head>):
     <link rel="stylesheet" href="/ui/app.css">
     <script src="/ui/shell.js"></script>
   y en el <body data-titulo="Vehículos">:
     <div class="app">
       <aside class="sidebar" id="sidebar" aria-label="Navegación principal"></aside>
       <div class="main"><header class="topbar" id="topbar"></header><main class="content">…</main></div>
     </div>
   Expone window.SMG con helpers (toast, confirmar, menús, formato es-AR, Chart.js). No declara otras globales.
   ===================================================================================================== */
(function () {
  "use strict";

  // ---------------------------------------------------------------- íconos (trazos estilo Lucide)
  const ICONOS = {
    home: '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
    dashboard: '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    "file-plus": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M9 15h6"/><path d="M12 18v-6"/>',
    clipboard: '<rect width="8" height="4" x="8" y="2" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="m9 14 2 2 4-4"/>',
    "file-text": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
    "bar-chart": '<path d="M3 3v18h18"/><path d="M7 16h8"/><path d="M7 11h12"/><path d="M7 6h3"/>',
    truck: '<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/><path d="M15 18H9"/><path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.62l-3.48-4.35A1 1 0 0 0 17.52 8H14"/><circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/>',
    machine: '<path d="M2 20h13"/><rect x="2" y="13" width="11" height="5" rx="1"/><path d="M5 13V9h4l3 4"/><path d="m12 11 5-6 4 3-3 3"/>',
    trailer: '<rect x="2" y="6" width="15" height="10" rx="1"/><circle cx="7" cy="18" r="2"/><circle cx="12" cy="18" r="2"/><path d="M17 13h5"/>',
    shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    settings: '<path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>',
    panel: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/><path d="m16 15-3-3 3-3"/>',
    menu: '<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
    more: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    sparkles: '<path d="M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.13-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.13a.5.5 0 0 1 .96 0L14.06 8.5A2 2 0 0 0 15.5 9.94l6.13 1.58a.5.5 0 0 1 0 .96L15.5 14.06a2 2 0 0 0-1.44 1.44l-1.58 6.13a.5.5 0 0 1-.96 0z"/>',
    refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    printer: '<path d="M6 9V2h12v7"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/>',
    columns: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/><path d="M15 3v18"/>',
    filter: '<path d="M22 3H2l8 9.46V19l4 2v-8.54z"/>',
    alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/>',
    "clipboard-check": '<rect width="8" height="4" x="8" y="2" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="m9 14 2 2 4-4"/>',
    "calendar-clock": '<path d="M8 2v4M16 2v4M3 10h12"/><rect x="3" y="4" width="18" height="17" rx="2"/><circle cx="17" cy="16" r="3"/><path d="M17 14.5V16l1 1"/>',
    info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    pencil: '<path d="M21.17 6.81a1 1 0 0 0-3.99-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.62l4.35-1.32a2 2 0 0 0 .83-.5z"/>',
    copy: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    trash: '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
    left: '<path d="m15 18-6-6 6-6"/>',
    right: '<path d="m9 18 6-6-6-6"/>',
    sort: '<path d="m21 16-4 4-4-4"/><path d="M17 20V4"/><path d="m3 8 4-4 4 4"/><path d="M7 4v16"/>',
    up: '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    down: '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
    inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    "file-signature": '<path d="M20 19.5v.5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8.5L18 5.5"/><path d="M8 18h1"/><path d="M18.42 9.61a2.1 2.1 0 1 1 2.97 2.97L16.95 17 13 18l.99-3.95 4.43-4.44Z"/>',
    receipt: '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/><path d="M12 17.5v-11"/>',
    building: '<path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"/><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"/><path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"/><path d="M10 6h4"/><path d="M10 10h4"/><path d="M10 14h4"/><path d="M10 18h4"/>',
    cart: '<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"/>',
    chevrons: '<path d="m7 15 5 5 5-5"/><path d="m7 9 5-5 5 5"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    ban: '<circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/>',
    help: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
    eye: '<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/><circle cx="12" cy="12" r="3"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "arrow-left-right": '<path d="M8 3 4 7l4 4"/><path d="M4 7h16"/><path d="m16 21 4-4-4-4"/><path d="M20 17H4"/>',
    history: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
    warehouse: '<path d="M22 8.35V20a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8.35A2 2 0 0 1 3.26 6.5l8-3.2a2 2 0 0 1 1.48 0l8 3.2A2 2 0 0 1 22 8.35Z"/><path d="M6 18h12"/><path d="M6 14h12"/><rect width="12" height="12" x="6" y="10"/>',
    package: '<path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"/><path d="M12 22V12"/><path d="m3.3 7 7.703 4.734a2 2 0 0 0 1.994 0L20.7 7"/><path d="m7.5 4.27 9 5.15"/>',
    database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/>',
    merge: '<path d="m8 6 4-4 4 4"/><path d="M12 2v10.3a4 4 0 0 1-1.172 2.872L4 22"/><path d="m20 22-5-5"/>',
    "clipboard-list": '<rect width="8" height="4" x="8" y="2" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/>',
    "calendar-days": '<rect width="18" height="18" x="3" y="4" rx="2"/><path d="M8 2v4"/><path d="M16 2v4"/><path d="M3 10h18"/><path d="M8 14h.01"/><path d="M12 14h.01"/><path d="M16 14h.01"/><path d="M8 18h.01"/><path d="M12 18h.01"/><path d="M16 18h.01"/>',
    briefcase: '<path d="M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/><rect width="20" height="14" x="2" y="6" rx="2"/>',
    ruler: '<path d="M21.3 15.3a2.4 2.4 0 0 1 0 3.4l-2.6 2.6a2.4 2.4 0 0 1-3.4 0L2.7 8.7a2.41 2.41 0 0 1 0-3.4l2.6-2.6a2.41 2.41 0 0 1 3.4 0Z"/><path d="m14.5 12.5 2-2"/><path d="m11.5 9.5 2-2"/><path d="m8.5 6.5 2-2"/><path d="m17.5 15.5 2-2"/>',
    landmark: '<path d="M10 18v-7"/><path d="M11.12 2.198a2 2 0 0 1 1.76.006l7.866 3.847c.476.233.31.949-.22.949H3.474c-.53 0-.695-.716-.22-.949z"/><path d="M14 18v-7"/><path d="M18 18v-7"/><path d="M3 22h18"/><path d="M6 18v-7"/>',
    "hard-hat": '<path d="M10 10V5a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v5"/><path d="M14 6a6 6 0 0 1 6 6v3"/><path d="M4 15v-3a6 6 0 0 1 6-6"/><rect x="2" y="15" width="20" height="4" rx="1"/>',
    upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
    image: '<rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    save: '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/><path d="M7 3v4a1 1 0 0 0 1 1h7"/>',
    eraser: '<path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/><path d="M22 21H7"/><path d="m5 11 9 9"/>',
    "file-input": '<path d="M4 22h14a2 2 0 0 0 2-2V7l-5-5H6a2 2 0 0 0-2 2v4"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M2 15h10"/><path d="m9 18 3-3-3-3"/>',
    wallet: '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1"/><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>',
    "book-user": '<path d="M15 13a3 3 0 1 0-6 0"/><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H19a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H6.5a1 1 0 0 1 0-5H20"/><circle cx="12" cy="8" r="2"/>',
    rocket: '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>',
    gear: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  };

  // ---------------------------------------------------------------- navegación por módulo
  // Cada módulo tiene su menú. Los ítems con hash (/compras#solicitudes) son paneles de una misma página.
  // tipos: tipos de usuario habilitados (además del panel); adminTodo: el administrador ve todos los paneles.
  const NAV = [
    { seccion: "Operación", items: [
      { k: "solicitud_viaje", l: "Solicitud de viaje", href: "/form", i: "file-plus" },
      { k: "asignar_recursos", l: "Asignar recursos", href: "/recursos_form", i: "clipboard" },
      { k: "ordenes_salida", l: "Órdenes de salida", href: "/ordenes_view", i: "file-text" },
    ] },
    { seccion: "Recursos", items: [
      { k: "vehiculos", l: "Vehículos", href: "/vehiculos_form", i: "truck" },
      { k: "personal", l: "Personal", href: "/personal_form", i: "users" },
    ] },
    { seccion: "Análisis", items: [
      { k: "dashboard", l: "Dashboard", href: "/dashboard", i: "dashboard" },
      { k: "gestion_operativa", l: "Gestión operativa", href: "/gestion_operativa", i: "bar-chart" },
    ] },
  ];
  const ADM_SUP = ["ADMINISTRADOR", "SUPERVISOR"];
  const NAV_COMPRAS = [
    { seccion: "Resumen", items: [
      { k: "dashboard", l: "Panel", href: "/compras#panel", i: "dashboard", tipos: [...ADM_SUP, "ASISTENTE"] },
    ] },
    { seccion: "Solicitudes", items: [
      { k: "solicitud_compra", l: "Nueva solicitud", href: "/compras#nueva-solicitud", i: "file-plus" },
      { k: "solicitudes_emitidas", l: "Solicitudes", href: "/compras#solicitudes", i: "inbox" },
    ] },
    { seccion: "Órdenes de compra", items: [
      { k: "emision_orden_compra", l: "Nueva orden de compra", href: "/compras#nueva-oc", i: "file-signature", tipos: ADM_SUP },
      { k: "ordenes_compra_emitidas", l: "Órdenes de compra", href: "/compras#ordenes", i: "receipt", tipos: ADM_SUP },
    ] },
    { seccion: "Datos maestros", items: [
      { k: "proveedores", l: "Proveedores", href: "/compras#proveedores", i: "building" },
    ] },
  ];
  // panel: permiso que habilita el ítem cuando no coincide con su clave (dos ítems de un mismo panel)
  const NAV_ALMACEN = [
    { seccion: "General", items: [
      { k: "dashboard", l: "Panel", href: "/almacen#panel", i: "dashboard" },
    ] },
    { seccion: "Operación", items: [
      { k: "ingresos_salidas", l: "Ingresos y salidas", href: "/almacen#ingresos", i: "arrow-left-right" },
      { k: "pendientes", panel: "ingresos_salidas", l: "Pendientes de autorización", href: "/almacen#pendientes", i: "clock" },
      { k: "documentos", l: "Documentos", href: "/almacen#documentos", i: "file-text" },
      { k: "movimientos", l: "Movimientos", href: "/almacen#movimientos", i: "history" },
    ] },
    { seccion: "Stock", items: [
      { k: "inventario", l: "Inventario", href: "/almacen#inventario", i: "warehouse" },
      { k: "productos", l: "Productos", href: "/almacen#productos", i: "package" },
    ] },
    { seccion: "Configuración", items: [
      { k: "catalogos", l: "Catálogos", href: "/almacen#catalogos", i: "database" },
    ] },
  ];
  // Operaciones: una sola página (/operaciones) con una vista por hash. alias: vistas sin ítem propio que marcan este ítem.
  const NAV_OPERACIONES = [
    { seccion: "General", items: [
      { k: "dashboard", l: "Panel", href: "/operaciones#panel", i: "dashboard" },
    ] },
    { seccion: "Campo", items: [
      { k: "carga_partes_diarios", l: "Partes diarios", href: "/operaciones#partes", i: "clipboard-list" },
      { k: "tarja_general", l: "Tarja general", href: "/operaciones#tarja", i: "calendar-days" },
    ] },
    { seccion: "Clientes", items: [
      { k: "seguimiento_cliente", l: "Seguimiento de cliente", href: "/operaciones#seguimiento", i: "briefcase", alias: ["certificados"] },
      { k: "actas_medicion", l: "Actas de medición", href: "/operaciones#actas", i: "ruler" },
    ] },
    { seccion: "Configuración", items: [
      { k: "centros_costos", l: "Centros de costos", href: "/operaciones#centros", i: "landmark" },
    ] },
  ];
  const NAV_MANTENIMIENTO = [
    { seccion: "General", items: [
      { k: "panel", panel: "indicadores", l: "Panel", href: "/mantenimiento#panel", i: "dashboard", alias: ["indicadores"] },
    ] },
    { seccion: "Operación", items: [
      { k: "novedades", l: "Novedades", href: "/mantenimiento#novedades", i: "bell" },
      { k: "ordenes", l: "Órdenes de trabajo", href: "/mantenimiento#ordenes", i: "wrench" },
      { k: "checklist", l: "Check list", href: "/mantenimiento#checklist", i: "clipboard-check" },
    ] },
    { seccion: "Flota", items: [
      { k: "equipos", l: "Equipos", href: "/mantenimiento#equipos", i: "truck" },
      { k: "preventivo", l: "Preventivo", href: "/mantenimiento#preventivo", i: "calendar-clock" },
    ] },
    { seccion: "Configuración", items: [
      { k: "configuracion", l: "Configuración", href: "/mantenimiento#configuracion", i: "settings" },
    ] },
  ];
  // Facturación: una sola página (/facturacion) con una vista por hash. El permiso es el del módulo (no tiene paneles).
  // pie: el grupo va en el pie de la sidebar (sin título), sobre los ítems fijos del shell.
  const NAV_FACTURACION = [
    { seccion: "General", items: [
      { k: "tablero", l: "Tablero de seguimiento", href: "/facturacion#tablero", i: "dashboard" },
    ] },
    { seccion: "Circuito", items: [
      { k: "pedidos", l: "Pedidos de facturación", href: "/facturacion#pedidos", i: "file-input" },
      { k: "facturas", l: "Facturas, ND y NC", href: "/facturacion#facturas", i: "receipt" },
      { k: "cobranzas", l: "Cobranzas y recibos", href: "/facturacion#cobranzas", i: "wallet" },
    ] },
    { seccion: "Clientes", items: [
      { k: "cuenta_corriente", l: "Cuenta corriente", href: "/facturacion#cuenta-corriente", i: "book-user" },
    ] },
    { seccion: "Puesta en marcha y configuración", pie: true, items: [
      { k: "puesta_en_marcha", l: "Puesta en marcha", href: "/facturacion#puesta-en-marcha", i: "rocket" },
      { k: "configuracion", l: "Configuración y permisos", href: "/facturacion#configuracion", i: "gear" },
    ] },
  ];
  // Administración: una sola página (/administracion) con una sección por hash. Solo la abre el administrador
  // (lo controla el backend); el permiso del ítem es el del módulo.
  const NAV_ADMINISTRACION = [
    { seccion: "Accesos", items: [
      { k: "usuarios", l: "Usuarios", href: "/administracion#usuarios", i: "users" },
      { k: "pin", l: "PIN de operadores", href: "/administracion#pin", i: "shield" },
    ] },
    { seccion: "Notificaciones", items: [
      { k: "responsables", l: "Responsables de módulo", href: "/administracion#responsables", i: "bell" },
      { k: "plantillas", l: "Plantillas de email", href: "/administracion#plantillas", i: "file-text" },
    ] },
    { seccion: "App de operadores", items: [
      { k: "pestanas", l: "Pestañas de la app", href: "/administracion#pestanas", i: "columns" },
    ] },
    { seccion: "Auditoría", items: [
      { k: "accesos", l: "Historial de accesos", href: "/administracion#accesos", i: "history" },
    ] },
  ];
  const MODULOS = [
    { k: "logistica", l: "Logística", i: "truck", nav: NAV, adminTodo: true },
    { k: "compras", l: "Compras", i: "cart", nav: NAV_COMPRAS, adminTodo: false },
    { k: "almacen", l: "Almacén", i: "warehouse", nav: NAV_ALMACEN, adminTodo: true },
    { k: "operaciones", l: "Operaciones", i: "hard-hat", nav: NAV_OPERACIONES, adminTodo: false },
    { k: "mantenimiento", l: "Mantenimiento", i: "wrench", nav: NAV_MANTENIMIENTO, adminTodo: false },
    { k: "facturacion", l: "Facturación", i: "receipt", nav: NAV_FACTURACION, adminTodo: true, sinPaneles: true },
    { k: "administracion", l: "Administración", i: "settings", nav: NAV_ADMINISTRACION, adminTodo: true, sinPaneles: true },
  ];

  // ---------------------------------------------------------------- utilidades
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const collator = new Intl.Collator("es", { numeric: true, sensitivity: "base" });
  const fmtNum = (n, dec = 0) => Number(n || 0).toLocaleString("es-AR", { minimumFractionDigits: dec, maximumFractionDigits: dec });
  const fmtFecha = (d) => d.toLocaleString("es-AR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  // Fechas de la base ("2026-09-01" o "2026-09-01 10:20:00") → "01/09/2026", sin pasar por la zona horaria.
  const fmtDia = (v) => { const m = String(v ?? "").match(/^(\d{4})-(\d{2})-(\d{2})/); return m ? `${m[3]}/${m[2]}/${m[1]}` : String(v ?? ""); };
  // Fecha y hora de la base → "01/09/2026 10:20". utc: el valor viene de CURRENT_TIMESTAMP / datetime('now') de SQLite.
  const fmtDiaHora = (v, { utc = false } = {}) => {
    const m = String(v ?? "").match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
    if (!m) return fmtDia(v);
    if (!utc) return `${m[3]}/${m[2]}/${m[1]} ${m[4]}:${m[5]}`;
    return fmtFecha(new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5])));
  };
  const fmtMonedaAR = new Intl.NumberFormat("es-AR", { style: "currency", currency: "ARS" });
  const fmtMoneda = (n) => fmtMonedaAR.format(Number(n) || 0);   // "$ 1.234,56"
  const icon = (n, cls = "") => `<svg class="i ${cls}" aria-hidden="true"><use href="#i-${n}"/></svg>`;
  const prefs = {
    get(k, d) { try { const v = localStorage.getItem("smg." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("smg." + k, JSON.stringify(v)); } catch { /* sin almacenamiento */ } },
  };
  const escapeHtml = (t) => (t ?? "").toString().replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const fold = (s) => (s ?? "").toString().normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const nivel = (pct) => (pct < 50 ? "danger" : pct <= 80 ? "warning" : "success");

  // Anchos de una tabla .tbl: las columnas { flex: true } (texto largo) van sin ancho y se reparten el espacio extra;
  // el resto conserva su ancho. min-width = suma de todas, así el scroll horizontal aparece solo cuando no entra.
  // clave (opcional): habilita el ancho manual (SMG.asasColumnas); los anchos ajustados se guardan por usuario/navegador
  // con esa clave y se respetan al repintar. Una columna ajustada a mano deja de ser flexible.
  function columnasTabla(tabla, colgroup, cols, { clave = "" } = {}) {
    const guardados = (clave && prefs.get(`anchos.${clave}`, null)) || {};
    const ancho = (c) => (c.k && guardados[c.k]) || c.w || 0;
    tabla.style.width = "";
    tabla.style.minWidth = `${cols.reduce((s, c) => s + ancho(c), 0)}px`;
    colgroup.innerHTML = cols.map((c) => (c.flex && !(c.k && guardados[c.k]) ? "<col>" : `<col style="width:${ancho(c)}px">`)).join("");
    if (clave) { tabla.dataset.anchos = clave; tabla._colsAnchos = cols; }
  }

  // Asas para ensanchar o achicar columnas a mano: arrastrar el borde derecho del encabezado, o foco + flechas
  // (Shift = de a 64px); doble clic restablece el ancho original. Llamar después de pintar el thead.
  function asasColumnas(tabla) {
    const clave = tabla.dataset.anchos;
    if (!clave || !tabla._colsAnchos || !tabla.tHead || !tabla.tHead.rows[0]) return;
    const sinHtml = (t) => String(t || "").replace(/<[^>]+>/g, "");
    [...tabla.tHead.rows[0].cells].forEach((th, i) => {
      const c = tabla._colsAnchos[i];
      if (!c || !c.k || !c.l || th.querySelector(".col-resize")) return;   // sin asa en las columnas de acciones
      th.insertAdjacentHTML("beforeend", `<span class="col-resize" role="separator" aria-orientation="vertical" tabindex="0" data-col="${i}"
        aria-label="Ancho de la columna ${escapeHtml(sinHtml(c.l))}" title="Arrastrá para cambiar el ancho · doble clic restablece"></span>`);
    });
    if (tabla._asasListas) return;
    tabla._asasListas = true;
    const MIN = 48;
    const colDe = (i) => tabla.querySelector("colgroup").children[i];
    const recalcular = () => {
      const cols = tabla._colsAnchos || [];
      tabla.style.minWidth = `${[...tabla.querySelector("colgroup").children].reduce((s, col, j) => s + (parseFloat(col.style.width) || (cols[j] && cols[j].w) || 0), 0)}px`;
    };
    const fijar = (i, px, guardar) => {
      const c = tabla._colsAnchos[i], w = Math.max(MIN, Math.round(px));
      colDe(i).style.width = `${w}px`;
      recalcular();
      if (guardar && c && c.k) prefs.set(`anchos.${tabla.dataset.anchos}`, { ...(prefs.get(`anchos.${tabla.dataset.anchos}`, null) || {}), [c.k]: w });
    };
    tabla.tHead.addEventListener("pointerdown", (e) => {
      const asa = e.target.closest(".col-resize");
      if (!asa || e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const i = Number(asa.dataset.col), x0 = e.clientX, w0 = asa.parentElement.getBoundingClientRect().width;
      let ultimo = w0;
      asa.classList.add("is-drag");
      document.body.classList.add("col-resizing");
      asa.setPointerCapture(e.pointerId);
      const mover = (ev) => { ultimo = w0 + ev.clientX - x0; fijar(i, ultimo, false); };
      const soltar = () => {
        asa.removeEventListener("pointermove", mover);
        asa.removeEventListener("pointerup", soltar);
        asa.removeEventListener("pointercancel", soltar);
        asa.classList.remove("is-drag");
        document.body.classList.remove("col-resizing");
        if (Math.abs(ultimo - w0) >= 1) fijar(i, ultimo, true);
      };
      asa.addEventListener("pointermove", mover);
      asa.addEventListener("pointerup", soltar);
      asa.addEventListener("pointercancel", soltar);
    });
    tabla.tHead.addEventListener("click", (e) => { if (e.target.closest(".col-resize")) e.stopPropagation(); }, true);   // no ordena
    tabla.tHead.addEventListener("dblclick", (e) => {
      const asa = e.target.closest(".col-resize");
      if (!asa) return;
      const i = Number(asa.dataset.col), c = tabla._colsAnchos[i];
      const g = { ...(prefs.get(`anchos.${tabla.dataset.anchos}`, null) || {}) };
      delete g[c.k];
      prefs.set(`anchos.${tabla.dataset.anchos}`, g);
      colDe(i).style.width = c.flex ? "" : `${c.w}px`;
      recalcular();
    });
    tabla.tHead.addEventListener("keydown", (e) => {
      const asa = e.target.closest(".col-resize");
      if (!asa || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
      e.preventDefault();
      const paso = (e.shiftKey ? 64 : 16) * (e.key === "ArrowRight" ? 1 : -1);
      fijar(Number(asa.dataset.col), asa.parentElement.getBoundingClientRect().width + paso, true);
    });
  }
  // Vuelve todas las columnas de una tabla a su ancho original
  const restablecerAnchos = (clave) => prefs.set(`anchos.${clave}`, null);

  async function pedirJson(url, opciones = {}) {
    const res = await fetch(url, { credentials: "same-origin", headers: opciones.body ? { "Content-Type": "application/json" } : {}, ...opciones });
    let datos = null;
    try { datos = await res.json(); } catch { /* sin JSON */ }
    if (!res.ok) throw new Error((datos && typeof datos.detail === "string" && datos.detail) || `Error ${res.status}`);
    if (datos && datos.error) throw new Error(datos.error);
    return datos;
  }

  function capa(id, html) {
    let el = document.getElementById(id);
    if (!el) { document.body.insertAdjacentHTML("beforeend", html); el = document.getElementById(id); }
    return el;
  }

  // link: { texto, href } agrega un enlace al final del mensaje (p. ej. «Ver solicitud»).
  function toast(msg, tipo = "ok", { link = null } = {}) {
    const zona = capa("toasts", '<div class="toasts" id="toasts" aria-live="polite"></div>');
    const el = document.createElement("div");
    el.className = `toast toast-${tipo}`;
    el.setAttribute("role", tipo === "error" ? "alert" : "status");
    el.innerHTML = `${icon(tipo === "error" ? "alert" : tipo === "info" ? "info" : "check")}<span>${escapeHtml(msg)}${
      link ? ` <a class="btn-link" href="${escapeHtml(link.href)}">${escapeHtml(link.texto)}</a>` : ""}</span>`;
    zona.appendChild(el);
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 220); }, tipo === "error" ? 6000 : link ? 6000 : 3500);
  }

  function confirmar(titulo, texto, { ok = "Confirmar", peligro = false } = {}) {
    const dlg = capa("confirm", `<div class="dialog" id="confirm" hidden>
      <div class="dialog-panel" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-text">
        <h2 id="confirm-title"></h2><p id="confirm-text"></p>
        <div class="dialog-actions"><button type="button" class="btn" id="confirm-cancel">Cancelar</button><button type="button" class="btn btn-primary" id="confirm-ok">Confirmar</button></div>
      </div></div>`);
    return new Promise((resolve) => {
      const bOk = $("#confirm-ok"), bNo = $("#confirm-cancel"), previo = document.activeElement;
      $("#confirm-title").textContent = titulo;
      $("#confirm-text").textContent = texto;
      bOk.textContent = ok;
      bOk.className = `btn ${peligro ? "btn-danger" : "btn-primary"}`;
      dlg.hidden = false;
      bNo.focus();
      const fin = (r) => { dlg.hidden = true; bOk.onclick = bNo.onclick = dlg.onkeydown = null; if (previo && previo.focus) previo.focus(); resolve(r); };
      bOk.onclick = () => fin(true);
      bNo.onclick = () => fin(false);
      dlg.onkeydown = (e) => { if (e.key === "Escape") { e.stopPropagation(); fin(false); } };
    });
  }
  const hayConfirmAbierto = () => { const d = document.getElementById("confirm"); return !!d && !d.hidden; };

  // Diálogo con un campo de texto (reemplaza prompt()). Devuelve el texto o null si se cancela.
  // multilinea: textarea (motivos, justificaciones) · requerido: no deja confirmar vacío · peligro: botón rojo.
  function pedirTexto(titulo, texto, { valor = "", etiqueta = "", ok = "Aplicar", mono = false, multilinea = false, requerido = false, peligro = false } = {}) {
    const dlg = capa("pedir", `<div class="dialog" id="pedir" hidden>
      <form class="dialog-panel" role="dialog" aria-modal="true" aria-labelledby="pedir-title" novalidate>
        <h2 id="pedir-title"></h2><p id="pedir-text"></p>
        <label class="field" style="margin-top:var(--s4)"><span id="pedir-label"></span><input class="input" id="pedir-input" autocomplete="off"><textarea class="textarea" id="pedir-area" rows="3"></textarea></label>
        <div class="dialog-actions"><button type="button" class="btn" id="pedir-cancel">Cancelar</button><button type="submit" class="btn btn-primary" id="pedir-ok"></button></div>
      </form></div>`);
    return new Promise((resolve) => {
      const form = dlg.querySelector("form"), previo = document.activeElement;
      const input = multilinea ? $("#pedir-area") : $("#pedir-input");
      $("#pedir-input").hidden = multilinea;
      $("#pedir-area").hidden = !multilinea;
      $("#pedir-title").textContent = titulo;
      $("#pedir-text").textContent = texto;
      $("#pedir-label").textContent = etiqueta;
      $("#pedir-label").className = requerido ? "req" : "";
      const bOk = $("#pedir-ok");
      bOk.textContent = ok;
      bOk.className = `btn ${peligro ? "btn-danger" : "btn-primary"}`;
      input.value = valor;
      input.classList.toggle("mono", mono);
      const validar = () => { bOk.disabled = requerido && !input.value.trim(); };
      input.oninput = validar;
      validar();
      dlg.hidden = false;
      input.focus(); input.select();
      const fin = (r) => { dlg.hidden = true; form.onsubmit = $("#pedir-cancel").onclick = dlg.onkeydown = input.oninput = null; if (previo && previo.focus) previo.focus(); resolve(r); };
      form.onsubmit = (e) => { e.preventDefault(); if (!bOk.disabled) fin(input.value.trim()); };
      if (multilinea) input.onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) form.requestSubmit(); };
      $("#pedir-cancel").onclick = () => fin(null);
      dlg.onkeydown = (e) => { if (e.key === "Escape") { e.stopPropagation(); fin(null); } };
    });
  }
  const hayDialogoAbierto = () => hayConfirmAbierto() || (!!document.getElementById("pedir") && !document.getElementById("pedir").hidden);

  // ---------------------------------------------------------------- menús flotantes
  let menuAbierto = null;
  function posicionarMenu(menu, ancla) {
    const r = ancla.getBoundingClientRect(), w = menu.offsetWidth, h = menu.offsetHeight;
    const top = r.bottom + 4 + h > window.innerHeight - 8 ? Math.max(8, r.top - h - 4) : r.bottom + 4;
    menu.style.top = `${top}px`;
    menu.style.left = `${Math.min(window.innerWidth - w - 8, Math.max(8, r.right - w))}px`;
  }
  // foco: false deja el foco en el ancla (listas que acompañan a un campo de texto mientras se escribe).
  function abrirMenu(menu, ancla, { foco = true, alternar = true } = {}) {
    if (menuAbierto && menuAbierto.menu === menu) {
      if (alternar) { cerrarMenu(); return; }
      posicionarMenu(menu, ancla);
      return;
    }
    cerrarMenu();
    menu.hidden = false;
    posicionarMenu(menu, ancla);
    ancla.setAttribute("aria-expanded", "true");
    menuAbierto = { menu, ancla };
    const primero = foco && menu.querySelector("input, button, a");
    if (primero) primero.focus();
  }

  // ---------------------------------------------------------------- combobox con búsqueda
  // Campo de texto + lista filtrable (proveedores, solicitantes, centros de costo…).
  // opciones(): [{ v, l, sub }] · onElegir(op | null) · crear: { l, onClick } agrega una acción al final de la lista
  // libre: acepta texto que no está en la lista (onElegir recibe { v: texto, l: texto, libre: true }).
  function combobox(input, { opciones, onElegir = () => {}, crear = null, libre = false, max = 60, vacio = "Sin coincidencias" }) {
    const lista = document.createElement("div");
    lista.className = "menu combo-list";
    lista.setAttribute("role", "listbox");
    lista.hidden = true;
    lista.id = `${input.id || "combo"}-lista`;
    document.body.appendChild(lista);
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-controls", lista.id);
    input.setAttribute("aria-expanded", "false");
    input.autocomplete = "off";
    let elegido = null, visibles = [], activo = -1;

    const pintar = () => {
      const terminos = fold(input.value).split(/\s+/).filter(Boolean);
      const todas = opciones() || [];
      const filtro = elegido && input.value === elegido.l ? [] : terminos;   // con un valor elegido, se ve la lista completa
      visibles = todas.filter((o) => { const t = fold(`${o.l} ${o.sub || ""}`); return filtro.every((x) => t.includes(x)); }).slice(0, max);
      activo = visibles.findIndex((o) => elegido && o.v === elegido.v);
      lista.innerHTML = (visibles.length
        ? visibles.map((o, i) => `<div class="menu-item combo-opt" role="option" id="${lista.id}-${i}" data-i="${i}" aria-selected="${i === activo}">
            <span class="trunc">${escapeHtml(o.l)}${o.sub ? `<small>${escapeHtml(o.sub)}</small>` : ""}</span>${elegido && o.v === elegido.v ? icon("check", "i-sm") : ""}</div>`).join("")
        : `<div class="menu-head">${escapeHtml(vacio)}</div>`)
        + (crear ? `<div class="menu-sep"></div><div class="menu-item combo-opt" role="option" data-crear>${icon("plus")}${escapeHtml(crear.l)}</div>` : "");
      lista.style.width = `${Math.max(input.offsetWidth, 240)}px`;
      marcar();
    };
    const marcar = () => {
      $$(".combo-opt[data-i]", lista).forEach((el) => el.classList.toggle("on", Number(el.dataset.i) === activo));
      const el = lista.querySelector(`[data-i="${activo}"]`);
      if (el) { el.scrollIntoView({ block: "nearest" }); input.setAttribute("aria-activedescendant", el.id); }
      else input.removeAttribute("aria-activedescendant");
    };
    const abrir = () => { pintar(); abrirMenu(lista, input, { foco: false, alternar: false }); };
    const elegir = (o, avisar = true) => {
      elegido = o;
      input.value = o ? o.l : "";
      input.classList.toggle("is-set", !!o);
      if (menuAbierto && menuAbierto.menu === lista) cerrarMenu();   // solo su lista: el foco ya puede estar en otro campo
      if (avisar) onElegir(o);
    };
    input.addEventListener("focus", abrir);
    input.addEventListener("click", () => { if (lista.hidden) abrir(); });
    input.addEventListener("input", () => { activo = -1; abrir(); if (activo < 0 && visibles.length && input.value.trim()) { activo = 0; marcar(); } });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (lista.hidden) { abrir(); return; }
        activo = Math.max(0, Math.min(visibles.length - 1, activo + (e.key === "ArrowDown" ? 1 : -1)));
        marcar();
      } else if (e.key === "Enter" && !lista.hidden) {
        e.preventDefault();
        if (activo >= 0 && visibles[activo]) elegir(visibles[activo]);
        else if (libre && input.value.trim()) elegir({ v: input.value.trim(), l: input.value.trim(), libre: true });
      } else if (e.key === "Tab" && menuAbierto && menuAbierto.menu === lista) cerrarMenu();
    });
    input.addEventListener("blur", () => setTimeout(() => {
      if (document.activeElement === input) return;
      const texto = input.value.trim();
      if (elegido && texto === elegido.l) return;
      if (!texto) { if (elegido) elegir(null); return; }
      const exacta = (opciones() || []).find((o) => fold(o.l) === fold(texto));
      if (exacta) elegir(exacta);
      else if (libre) elegir({ v: texto, l: texto, libre: true });
      else elegir(elegido, false);   // vuelve al último valor válido
    }, 120));
    // la lista es fija: acompaña al campo si se desplaza la página o el contenedor (drawer, tabla)
    window.addEventListener("scroll", (e) => { if (!lista.hidden && !lista.contains(e.target)) posicionarMenu(lista, input); }, true);
    lista.addEventListener("mousedown", (e) => e.preventDefault());   // no le saca el foco al campo
    lista.addEventListener("click", (e) => {
      if (e.target.closest("[data-crear]")) { cerrarMenu(); crear.onClick(input.value.trim()); return; }
      const op = e.target.closest("[data-i]");
      if (op) elegir(visibles[Number(op.dataset.i)]);
    });
    return {
      get valor() { return elegido; },
      fijar(v) { const o = v == null || v === "" ? null : (opciones() || []).find((x) => String(x.v) === String(v)) || (libre ? { v, l: String(v), libre: true } : null); elegir(o, false); },
      limpiar() { elegir(null, false); },
    };
  }
  function cerrarMenu(devolverFoco = false) {
    if (!menuAbierto) return;
    menuAbierto.menu.hidden = true;
    menuAbierto.ancla.setAttribute("aria-expanded", "false");
    if (devolverFoco) menuAbierto.ancla.focus();
    menuAbierto = null;
  }
  const menuActual = () => menuAbierto;

  // ---------------------------------------------------------------- Chart.js con el tema
  function configurarChart() {
    if (typeof Chart === "undefined") return false;
    Chart.defaults.font.family = css("--font");
    Chart.defaults.font.size = 12;
    Chart.defaults.color = css("--text-muted");
    Chart.defaults.borderColor = css("--border");
    Object.assign(Chart.defaults.plugins.tooltip, {
      backgroundColor: css("--surface-2"), borderColor: css("--border-strong"), borderWidth: 1,
      titleColor: css("--text"), bodyColor: css("--text"), padding: 8, cornerRadius: 6, displayColors: false,
      titleFont: { weight: "600" },
    });
    return true;
  }

  // ---------------------------------------------------------------- buscador de la topbar
  let buscador = null;   // { placeholder, onInput } que registra la página; sin él, salta entre módulos
  function registrarBuscador(placeholder, onInput) {
    buscador = { placeholder, onInput };
    const input = document.getElementById("buscar-global");
    if (input) { input.placeholder = placeholder; input.removeAttribute("list"); }
  }

  // ---------------------------------------------------------------- usuario y permisos
  const usuario = fetch("/me", { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  const esAdmin = (me) => !!me && (String(me.tipo_usuario || "").toUpperCase() === "ADMINISTRADOR" || !!me.es_desarrollador);
  const moduloDe = (k) => MODULOS.find((m) => m.k === k) || MODULOS[0];
  function puedePanel(me, panel, modulo = "logistica") {
    if (!me) return true;          // si /me falla, no se oculta nada (el backend igual controla)
    if (me.es_desarrollador) return true;
    if (moduloDe(modulo).adminTodo && esAdmin(me)) return true;
    if (moduloDe(modulo).sinPaneles) return puedeModulo(me, moduloDe(modulo));
    return ((me.paneles && me.paneles[modulo]) || []).map((p) => String(p).toLowerCase()).includes(String(panel).toLowerCase());
  }
  function puedeItem(me, it, modulo) {
    if (!puedePanel(me, it.panel || it.k, modulo)) return false;
    return !it.tipos || !me || me.es_desarrollador || it.tipos.includes(String(me.tipo_usuario || "").toUpperCase());
  }
  // Administración: solo con el módulo habilitado en /me (ni el tipo ADMINISTRADOR ni desarrollador alcanzan).
  const tieneAdministracion = (me) => !!me && Array.isArray(me.modulos) && me.modulos.includes("administracion");
  const puedeModulo = (me, m) => m.k === "administracion" ? tieneAdministracion(me) : (!me || esAdmin(me) || (me.modulos || []).includes(m.k));

  // ---------------------------------------------------------------- armado
  const partesHref = (href) => { const [ruta, hash = ""] = href.split("#"); return { ruta, hash }; };
  function itemActual() {
    const ruta = location.pathname.replace(/\/+$/, "") || "/";
    const hash = location.hash.replace(/^#\/?/, "").split(/[/?]/)[0];
    if (ruta.startsWith("/mantenimiento/ot/")) {
      const item = NAV_MANTENIMIENTO.flatMap((g) => g.items).find((it) => it.k === "ordenes");
      return { ...item, seccion: "Operación", modulo: "mantenimiento" };
    }
    for (const m of MODULOS) for (const g of m.nav) for (const it of g.items) {
      const p = partesHref(it.href);
      if (p.hash ? ruta === p.ruta && (hash === p.hash || (it.alias || []).includes(hash)) : ruta === p.ruta || ruta.startsWith(p.ruta + "/")) return { ...it, seccion: g.seccion, modulo: m.k };
    }
    return null;
  }
  // Módulo de la página: el del ítem actual o, en un panel sin ítem propio (detalle), el de su ruta.
  function moduloActual() {
    const it = itemActual();
    if (it) return it.modulo;
    const ruta = location.pathname.replace(/\/+$/, "") || "/";
    const m = MODULOS.find((x) => x.nav.some((g) => g.items.some((i) => partesHref(i.href).ruta === ruta)));
    return m ? m.k : "logistica";
  }

  let moduloVisible = null;   // módulo cuyos ítems muestra la sidebar (lo cambia el selector, sin navegar)
  let migasExtra = [];        // tramos agregados por la página (p. ej. el N.º de una OC abierta)
  let meCache = null;
  const contadores = {};      // contador junto a un ítem de la sidebar (p. ej. pendientes de autorización)

  function pintarNav() {
    const nav = document.getElementById("sb-nav-items");
    if (!nav) return;
    const m = moduloDe(moduloVisible);
    const actual = itemActual();
    const item = (it) => {
      const c = contadores[`${m.k}.${it.k}`];
      const punto = c && typeof c === "object" ? c : null;   // { punto: true, tipo: "warning", titulo } → punto de estado
      const n = punto ? 0 : c;
      const titulo = punto && punto.titulo ? `${it.l} · ${punto.titulo}` : `${it.l}${n ? ` (${n})` : ""}`;
      const badge = punto ? `<span class="sb-badge sb-dot sb-dot-${escapeHtml(punto.tipo || "warning")}" role="img" aria-label="${escapeHtml(punto.titulo || "")}"></span>`
        : n ? `<span class="count-badge sb-badge">${n}</span>` : "";
      return `<a class="sb-item" href="${it.href}" data-panel="${it.k}" title="${escapeHtml(titulo)}" ${actual && actual.modulo === m.k && actual.k === it.k ? 'aria-current="page"' : ""}>${icon(it.i)}<span class="sb-label">${it.l}</span>${badge}</a>`;
    };
    nav.innerHTML = m.nav.filter((g) => !g.pie).map((g) => `<div class="sb-grupo" data-grupo><div class="sb-section">${g.seccion}</div>${g.items.map(item).join("")}</div>`).join("");
    const pie = document.getElementById("sb-foot-items");
    if (pie) pie.innerHTML = m.nav.filter((g) => g.pie).map((g) => `<div class="sb-grupo" data-grupo aria-label="${escapeHtml(g.seccion)}" role="group">${g.items.map(item).join("")}</div>`).join("");
    const sel = document.getElementById("sb-modulo");
    if (sel) sel.innerHTML = `${icon(m.i)}<span class="sb-label">${m.l}</span>${icon("chevrons", "i-sm sb-label")}`;
    if (sel) sel.title = `Módulo: ${m.l}`;
    aplicarPermisos();
  }
  function aplicarPermisos() {
    const me = meCache;
    if (!me) return;
    const m = moduloDe(moduloVisible);
    $$("#sidebar [data-panel]").forEach((a) => {
      const it = m.nav.flatMap((g) => g.items).find((x) => x.k === a.dataset.panel);
      a.hidden = !(it && puedeItem(me, it, m.k));
    });
    $$("#sidebar [data-grupo]").forEach((g) => { g.hidden = !g.querySelector("[data-panel]:not([hidden])"); });
    $$("#menu-modulos [data-modulo]").forEach((b) => { b.hidden = !puedeModulo(me, moduloDe(b.dataset.modulo)); });
  }
  function pintarMigas() {
    const crumbs = document.getElementById("crumbs");
    if (!crumbs) return;
    const actual = itemActual();
    const mod = moduloDe(moduloActual());
    const titulo = document.body.dataset.titulo || (actual && actual.l) || document.title;
    const tramos = [{ t: mod.l }, { t: titulo, href: migasExtra.length && actual ? actual.href : "" }, ...migasExtra.map((t) => ({ t }))];
    crumbs.innerHTML = tramos.map((x, i) => {
      const ultimo = i === tramos.length - 1;
      const txt = escapeHtml(x.t);
      return (ultimo ? `<b>${txt}</b>` : x.href ? `<a href="${escapeHtml(x.href)}">${txt}</a>` : `<span>${txt}</span>`) + (ultimo ? "" : icon("right", "i-sm"));
    }).join("");
  }
  // Contador de un ítem de la sidebar (0 lo oculta): SMG.contador("almacen", "pendientes", 3).
  // Con un objeto muestra un punto de estado: SMG.contador("facturacion", "puesta_en_marcha", { punto: true, tipo: "warning", titulo: "…" }); null lo oculta.
  function contador(modulo, k, n) {
    contadores[`${modulo}.${k}`] = n && typeof n === "object" ? n : Number(n) || 0;
    pintarNav();
  }
  // Chip de estado en la topbar, junto a las migas (p. ej. «Puesta en marcha: fecha pendiente»). null lo quita.
  // cfg: { texto, href, titulo, tipo: "warn" | "ok" | "bad" | "accent" | "muted" }
  let chipPendiente;
  function chipEstado(cfg) {
    const crumbs = document.getElementById("crumbs");
    if (!crumbs) { chipPendiente = cfg; return; }   // la topbar todavía no se montó
    let el = document.getElementById("topbar-estado");
    if (!cfg) { if (el) el.remove(); return; }
    if (!el) { crumbs.insertAdjacentHTML("afterend", '<a class="chip topbar-chip" id="topbar-estado"></a>'); el = document.getElementById("topbar-estado"); }
    el.className = `chip chip-${cfg.tipo || "warn"} topbar-chip`;
    el.href = cfg.href || "#";
    el.title = cfg.titulo || cfg.texto || "";
    el.innerHTML = `${icon("alert", "i-sm")}<span class="trunc">${escapeHtml(cfg.texto || "")}</span>`;
  }
  // La página llama a navegacion() después de cambiar de panel (hash) o para sumar tramos a las migas.
  function navegacion(extra = []) {
    migasExtra = extra;
    moduloVisible = moduloActual();
    pintarNav();
    pintarMigas();
  }

  function montar() {
    if (document.getElementById("i-home")) return;   // ya montado
    if (!document.querySelector('link[rel="icon"]')) document.head.insertAdjacentHTML("beforeend", '<link rel="icon" href="/Imagenes/smg-icono-192.png">');
    document.body.insertAdjacentHTML("afterbegin", `<svg width="0" height="0" style="position:absolute" aria-hidden="true">${
      Object.entries(ICONOS).map(([k, p]) => `<symbol id="i-${k}" viewBox="0 0 24 24">${p}</symbol>`).join("")}</svg>`);
    if (prefs.get("sbColapsada", false)) document.body.classList.add("sb-collapsed");
    moduloVisible = moduloActual();

    const sidebar = document.getElementById("sidebar");
    if (sidebar) {
      sidebar.innerHTML = `
        <a class="sb-brand" href="/" title="Menú principal"><img src="/Imagenes/smg-icono-192.png" alt="SMG"><span><b>SMG</b><small>Operaciones</small></span></a>
        <div class="sb-modulo-wrap"><button type="button" class="sb-item sb-modulo" id="sb-modulo" aria-haspopup="menu" aria-expanded="false"></button></div>
        <nav class="sb-nav">
          <a class="sb-item" href="/" title="Menú principal">${icon("home")}<span class="sb-label">Menú principal</span></a>
          <div id="sb-nav-items" class="sb-nav-items"></div>
        </nav>
        <div class="sb-foot">
          <div id="sb-foot-items" class="sb-foot-items"></div>
          <a class="sb-item" href="/administracion" id="sb-config" title="Configuración" hidden>${icon("settings")}<span class="sb-label">Configuración</span></a>
          <button type="button" class="sb-item" id="sb-toggle" title="Contraer / expandir menú" aria-label="Contraer menú">${icon("panel")}<span class="sb-label">Contraer</span></button>
        </div>`;
      document.body.insertAdjacentHTML("beforeend", `<div class="menu" id="menu-modulos" role="menu" hidden>
          <div class="menu-head">Cambiar de módulo</div>
          ${MODULOS.map((m) => `<button type="button" class="menu-item" role="menuitemradio" data-modulo="${m.k}"${m.k === "administracion" ? " hidden" : ""}>${icon(m.i)}<span class="trunc">${m.l}</span></button>`).join("")}</div>`);
      $("#sb-modulo").addEventListener("click", (e) => {
        $$("#menu-modulos [data-modulo]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.modulo === moduloVisible)));
        abrirMenu($("#menu-modulos"), e.currentTarget);
      });
      $("#menu-modulos").addEventListener("click", (e) => {
        const b = e.target.closest("[data-modulo]");
        if (!b) return;
        moduloVisible = b.dataset.modulo;
        cerrarMenu(true);
        pintarNav();
      });
      pintarNav();
      $("#sb-toggle").addEventListener("click", () => {
        const c = document.body.classList.toggle("sb-collapsed");
        prefs.set("sbColapsada", c);
        $("#sb-toggle").setAttribute("aria-label", c ? "Expandir menú" : "Contraer menú");
      });
    }

    const topbar = document.getElementById("topbar");
    if (topbar) {
      topbar.innerHTML = `
        <button type="button" class="btn btn-ghost btn-icon" id="sb-open" aria-label="Abrir menú">${icon("menu")}</button>
        <nav class="crumbs" id="crumbs" aria-label="Ubicación"></nav>
        <div class="search-global" role="search">${icon("search")}
          <input id="buscar-global" class="input" type="search" autocomplete="off" aria-label="Buscar"
            placeholder="${escapeHtml(buscador ? buscador.placeholder : "Ir a un módulo…")}" ${buscador ? "" : 'list="smg-modulos"'}>
          <span class="kbd" aria-hidden="true">Ctrl K</span>
          <datalist id="smg-modulos">${NAV.flatMap((g) => g.items).map((it) => `<option value="${it.l}">`).join("")}</datalist>
        </div>
        <button type="button" class="avatar" id="btn-usuario" aria-haspopup="menu" aria-label="Usuario">··</button>`;
      pintarMigas();
      if (chipPendiente !== undefined) chipEstado(chipPendiente);
      document.body.insertAdjacentHTML("beforeend", `<div class="menu" id="menu-usuario" role="menu" hidden>
          <div class="menu-head" id="usuario-nombre"></div>
          <a class="menu-item" role="menuitem" href="/">${icon("home")}Menú principal</a>
          <a class="menu-item" role="menuitem" href="/logout">${icon("logout")}Cerrar sesión</a></div>`);
      $("#btn-usuario").addEventListener("click", (e) => abrirMenu($("#menu-usuario"), e.currentTarget));
      $("#sb-open").addEventListener("click", () => document.body.classList.toggle("sb-mobile-open"));
      const input = $("#buscar-global");
      input.addEventListener("input", () => {
        if (buscador) { buscador.onInput(input.value); return; }
        const destino = NAV.flatMap((g) => g.items).find((it) => fold(it.l) === fold(input.value.trim()));
        if (destino && !$(`#sidebar [data-panel="${destino.k}"]`)?.hidden) location.href = destino.href;
      });
    }

    window.addEventListener("hashchange", () => navegacion([]));

    usuario.then((me) => {
      if (!me || !me.autenticado) return;
      meCache = me;
      aplicarPermisos();
      const cfg = document.getElementById("sb-config");
      if (cfg) cfg.hidden = !tieneAdministracion(me);
      const nombre = String(me.nombre_apellido || me.correo || "Usuario").trim();
      const ini = nombre.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("") || "U";
      const av = document.getElementById("btn-usuario");
      if (av) { av.textContent = ini; av.title = nombre; }
      const nom = document.getElementById("usuario-nombre");
      if (nom) nom.textContent = nombre;
    });

    configurarChart();
  }

  // cierre de menús, Escape y Ctrl+K: se registran al cargar (en captura) para correr antes que los de la página
  function enlazarGlobales() {
    document.addEventListener("click", (e) => {
      if (document.body.classList.contains("sb-mobile-open") && !e.target.closest("#sidebar, #sb-open")) document.body.classList.remove("sb-mobile-open");
    });
    document.addEventListener("mousedown", (e) => {
      if (menuAbierto && !menuAbierto.menu.contains(e.target) && !menuAbierto.ancla.contains(e.target)) cerrarMenu();
    });
    document.addEventListener("click", (e) => { if (e.target.closest("[data-cerrar-menu]")) cerrarMenu(true); });
    window.addEventListener("resize", () => cerrarMenu());
    document.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        const input = document.getElementById("buscar-global");
        if (input) { e.preventDefault(); input.focus(); input.select(); }
        return;
      }
      if (e.key !== "Escape" || hayDialogoAbierto()) return;
      if (menuAbierto) { cerrarMenu(true); e.stopImmediatePropagation(); return; }
      if (document.body.classList.contains("sb-mobile-open")) { document.body.classList.remove("sb-mobile-open"); e.stopImmediatePropagation(); }
    }, true);
  }
  enlazarGlobales();

  window.SMG = {
    $, $$, css, collator, fmtNum, fmtFecha, fmtDia, fmtDiaHora, fmtMoneda, icon, prefs, escapeHtml, fold, nivel, pedirJson,
    toast, confirmar, pedirTexto, hayConfirmAbierto, hayDialogoAbierto, abrirMenu, cerrarMenu, posicionarMenu, menuActual,
    combobox, configurarChart, registrarBuscador, columnasTabla, asasColumnas, restablecerAnchos, usuario, puedePanel, esAdmin, montar, navegacion, contador, chipEstado, NAV, MODULOS,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", montar);
  else montar();
})();
