<?php
/**
 * Front controller: listado
 * Devuelve los productos de la tienda (id, ean13, nombre, fabricante, activo).
 *
 * Endpoint:
 *   {shop}/index.php?fc=module&module=aplproveedoresconector&controller=listado
 *
 * Cuerpo (JSON, POST):  { "token": "...", "solo_con_ean": true }
 */

if (!defined('_PS_VERSION_')) {
    exit;
}

class AplproveedoresconectorListadoModuleFrontController extends ModuleFrontController
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

        $idLang = (int) Configuration::get('PS_LANG_DEFAULT');
        $soloConEan = !isset($in['solo_con_ean']) || $in['solo_con_ean'];

        $sql = 'SELECT p.id_product, p.ean13, p.active, pl.name, m.name AS fabricante
                FROM ' . _DB_PREFIX_ . 'product p
                JOIN ' . _DB_PREFIX_ . 'product_lang pl
                  ON pl.id_product = p.id_product AND pl.id_lang = ' . $idLang . '
                LEFT JOIN ' . _DB_PREFIX_ . 'manufacturer m
                  ON m.id_manufacturer = p.id_manufacturer';
        if (Shop::isFeatureActive()) {
            $sql .= ' AND pl.id_shop = ' . (int) Context::getContext()->shop->id;
        }
        if ($soloConEan) {
            $sql .= " WHERE p.ean13 <> ''";
        }
        $sql .= ' GROUP BY p.id_product ORDER BY m.name, pl.name';

        $incluirPrecio = !isset($in['precio']) || $in['precio'];  // por defecto, incluir PVP
        $rows = Db::getInstance()->executeS($sql);
        $productos = [];
        foreach ($rows as $r) {
            $item = [
                'id' => (int) $r['id_product'],
                'ean13' => $r['ean13'],
                'nombre' => $r['name'],
                'fabricante' => $r['fabricante'] !== null ? $r['fabricante'] : '',
                'activo' => (int) $r['active'] === 1,
            ];
            if ($incluirPrecio) {
                $idp = (int) $r['id_product'];
                $item['precio'] = round((float) Product::getPriceStatic($idp, true), 2);        // PVP con IVA
                $item['precio_sin_iva'] = round((float) Product::getPriceStatic($idp, false), 2);
            }
            $productos[] = $item;
        }

        $this->responder(['success' => true, 'total' => count($productos), 'productos' => $productos]);
    }

    private function responder(array $data, int $code = 200)
    {
        header('Content-Type: application/json; charset=utf-8');
        http_response_code($code);
        die(json_encode($data, JSON_UNESCAPED_UNICODE));
    }
}
