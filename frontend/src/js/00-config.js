window.NEURAL_STRATA_API = window.NEURAL_STRATA_API || '';
window.apiUrl = function(path) {
  var b = '';
  try {
    var q = new URLSearchParams(location.search).get('api');
    if (q) b = q;
    else if (typeof window.NEURAL_STRATA_API === 'string' && window.NEURAL_STRATA_API) b = window.NEURAL_STRATA_API;
    else if (location.protocol === 'file:' || (location.port && location.port !== '5000')) {
      b = 'http://localhost:5000';
    }
  } catch (e) {}
  return String(b || '').replace(/\/+$/, '') + path;
};

