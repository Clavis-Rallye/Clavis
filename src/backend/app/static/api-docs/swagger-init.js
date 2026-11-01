// Starts Swagger UI on /api/docs (app/api_docs.py). It is a file of its own,
// not an inline script, so the page needs no 'unsafe-inline' for scripts.
// validatorUrl null: the default sends the address of the specification to
// validator.swagger.io, a third party.
(function () {
  var root = document.getElementById('swagger-ui');
  window.ui = window.SwaggerUIBundle({
    url: root.getAttribute('data-openapi-url'),
    dom_id: '#swagger-ui',
    layout: 'BaseLayout',
    deepLinking: true,
    showExtensions: true,
    showCommonExtensions: true,
    validatorUrl: null,
    presets: [window.SwaggerUIBundle.presets.apis, window.SwaggerUIBundle.SwaggerUIStandalonePreset],
  });
})();
