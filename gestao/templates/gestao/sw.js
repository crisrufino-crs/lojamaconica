// Roda no celular em segundo plano. Ao receber o "toque" do sistema, busca os avisos e mostra.
self.addEventListener('push', function (evento) {
  evento.waitUntil(
    fetch('/push/pendentes/', { credentials: 'include' })
      .then(function (r) { return r.ok && r.headers.get('content-type').indexOf('json') > -1 ? r.json() : { avisos: [] }; })
      .catch(function () { return { avisos: [] }; })
      .then(function (dados) {
        var avisos = dados.avisos.length ? dados.avisos : [{ titulo: 'Novo aviso da Loja', texto: 'Abra o sistema para ver.' }];
        return Promise.all(avisos.map(function (a) {
          return self.registration.showNotification(a.titulo, { body: a.texto, icon: '/static/gestao/icone-192.png', badge: '/static/gestao/icone-192.png' });
        }));
      })
  );
});
self.addEventListener('notificationclick', function (evento) {
  evento.notification.close();
  evento.waitUntil(clients.openWindow('/notificacoes/'));
});
