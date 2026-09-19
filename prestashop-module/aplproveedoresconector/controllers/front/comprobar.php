<?php
/**
 * Front controller: comprobar
 * Indica cuáles de los EANs recibidos YA existen en la tienda.
 *
 * Busca por el EAN13 del producto y TAMBIÉN por su EAN de expositor (tabla
 * `<prefijo>apl_product_expositor`), de modo que un producto guardado en la
 * tienda con un EAN distinto al del proveedor también se encuentra.
 * La respuesta se indexa por el EAN preguntado (venga por donde venga).
 *
 * Endpoint:
 *   {shop}/index.php?fc=module&module=aplproveedoresconector&controller=comprobar
 * Cuerpo (JSON, POST):  { "token": "...", "eans": ["...","..."] }
 * Respuesta:
 *   { "success": true, "existentes": { "<ean preguntado>": {
 *       "id":N, "activo":bool, "nombre":"...", "precio":9.16,
 *       "ean13":"...", "ean_expositor":"...", "por":"ean13|expositor" } } }
 *   ("precio" = PVP con IVA incluido, redondeado a 2 decimales.)
 */

if (!defined('_PS_VERSION_')) {
    exit;
}

class AplproveedoresconectorComprobarModuleFrontController extends ModuleFrontController
{
    public $auth = false;
    public $guestAllowed = true;
    public $ssl = true;

    public function initContent()
    {
        parent::initContent();

        $raw = Tools::file_get_contents('php://input');
        $in = json_decode($raw, true);
        if (!is_array($in)) {
            $in = [];
        }
        $token = isset($in['token']) ? $in['token'] : Tools::getValue('token');
        if (!hash_equals((string) Configuration::get('APLPROVEEDORESCONECTOR_TOKEN'), (string) $token)) {
            $this->responder(['success' => false, 'error' => 'Token no autorizado'], 401);
        }

        // Saneamos los EANs a solo dígitos
        $eans = isset($in['eans']) && is_array($in['eans']) ? $in['eans'] : [];
        $limpios = [];
        foreach ($eans as $e) {
            $e = preg_replace('/\D/', '', (string) $e);
            if ($e !== '') {
                $limpios[$e] = true;
            }
        }
        $pedidos = $limpios;              // conjunto de EANs preguntados
        $limpios = array_keys($limpios);

        $existentes = [];
        if (!empty($limpios)) {
            $idLang = (int) Configuration::get('PS_LANG_DEFAULT');
            $tablaExp = $this->module->tablaExpositor();
            $in_sql = implode(',', array_map(function ($e) {
                return '"' . pSQL($e) . '"';
            }, $limpios));

            $sql = 'SELECT p.id_product, p.ean13, p.active, pl.name'
                 . ($tablaExp ? ', ex.ean_expositor' : '') . '
                    FROM ' . _DB_PREFIX_ . 'product p
                    JOIN ' . _DB_PREFIX_ . 'product_lang pl
                      ON pl.id_product = p.id_product AND pl.id_lang = ' . $idLang;
            if ($tablaExp) {
                $sql .= ' LEFT JOIN ' . $tablaExp . ' ex ON ex.id_product = p.id_product';
                $sql .= ' WHERE (p.ean13 IN (' . $in_sql . ') OR ex.ean_expositor IN (' . $in_sql . '))';
            } else {
                $sql .= ' WHERE p.ean13 IN (' . $in_sql . ')';
            }
            $sql .= ' GROUP BY p.id_product';

            foreach (Db::getInstance()->executeS($sql) as $r) {
                $idp = (int) $r['id_product'];
                $ean13 = (string) $r['ean13'];
                $eanExp = isset($r['ean_expositor']) && $r['ean_expositor'] !== null
                    ? (string) $r['ean_expositor'] : '';
                $ficha = [
                    'id' => $idp,
                    'activo' => (int) $r['active'] === 1,
                    'nombre' => $r['name'],
                    'precio' => round((float) Product::getPriceStatic($idp, true), 2), // PVP con IVA
                    'ean13' => $ean13,
                    'ean_expositor' => $eanExp,
                ];
                // Se indexa por cada EAN preguntado que apunte a este producto.
                if ($ean13 !== '' && isset($pedidos[$ean13])) {
                    $existentes[$ean13] = array_merge($ficha, ['por' => 'ean13']);
                }
                if ($eanExp !== '' && isset($pedidos[$eanExp]) && !isset($existentes[$eanExp])) {
                    $existentes[$eanExp] = array_merge($ficha, ['por' => 'expositor']);
                }
            }
        }

        $this->responder(['success' => true, 'existentes' => $existentes]);
    }

    private function responder(array $data, int $code = 200)
    {
        header('Content-Type: application/json; charset=utf-8');
        http_response_code($code);
        die(json_encode($data, JSON_UNESCAPED_UNICODE));
    }
}
