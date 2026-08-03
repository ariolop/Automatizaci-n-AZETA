<?php
/**
 * Front controller: ficha
 * Devuelve la ficha resumida de un producto por EAN: nombre, precio (con y sin
 * IVA), imagen de portada y si está activo. Para mostrar en la app cómo está el
 * producto en PrestaShop.
 *
 * Endpoint:
 *   {shop}/index.php?fc=module&module=aplproveedoresconector&controller=ficha
 *
 * Cuerpo (JSON, POST): { "token": "...", "ean": "8411..." }
 */

if (!defined('_PS_VERSION_')) {
    exit;
}

class AplproveedoresconectorFichaModuleFrontController extends ModuleFrontController
{
    public $auth = false;
    public $guestAllowed = true;
    public $ssl = true;

    public function initContent()
    {
        parent::initContent();
        $this->procesar();
    }

    private function procesar()
    {
        $raw = Tools::file_get_contents('php://input');
        $in = json_decode($raw, true);

        if (!is_array($in)) {
            $this->responder(['success' => false, 'error' => 'JSON inválido'], 400);
        }
        if (!hash_equals((string) Configuration::get('APLPROVEEDORESCONECTOR_TOKEN'), (string) ($in['token'] ?? ''))) {
            $this->responder(['success' => false, 'error' => 'Token no autorizado'], 401);
        }

        $ean = preg_replace('/\D/', '', (string) ($in['ean'] ?? ''));
        if ($ean === '') {
            $this->responder(['success' => false, 'error' => 'Falta ean'], 400);
        }

        $idLang = (int) Configuration::get('PS_LANG_DEFAULT');
        $id = (int) Db::getInstance()->getValue(
            'SELECT id_product FROM ' . _DB_PREFIX_ . 'product WHERE ean13 = "' . pSQL($ean) . '"'
        );
        if (!$id) {
            $this->responder(['success' => true, 'encontrado' => false]);
        }

        $product = new Product($id, false, $idLang);
        if (!Validate::isLoadedObject($product)) {
            $this->responder(['success' => true, 'encontrado' => false]);
        }

        $nombre = is_array($product->name) ? ($product->name[$idLang] ?? reset($product->name)) : $product->name;
        $rewrite = is_array($product->link_rewrite) ? ($product->link_rewrite[$idLang] ?? 'p') : $product->link_rewrite;

        // Imagen de portada (URL absoluta, best-effort)
        $imagen = '';
        try {
            $cover = Image::getCover($id);
            if ($cover && !empty($cover['id_image'])) {
                $link = Context::getContext()->link;
                $imagen = $link->getImageLink($rewrite ?: 'p', (int) $cover['id_image'], 'home_default');
                if ($imagen && strpos($imagen, 'http') !== 0) {
                    $imagen = 'https://' . ltrim($imagen, '/');
                }
            }
        } catch (Exception $e) {
            $imagen = '';
        }

        // Stock disponible (por producto, todas las combinaciones)
        $stock = (int) StockAvailable::getQuantityAvailableByProduct($id);

        $this->responder([
            'success' => true,
            'encontrado' => true,
            'id_product' => $id,
            'nombre' => $nombre,
            'activo' => (bool) $product->active,
            'precio' => round((float) Product::getPriceStatic($id, true), 2),
            'precio_sin_iva' => round((float) Product::getPriceStatic($id, false), 2),
            'iva_pct' => round((float) $product->getTaxesRate(), 2),
            'id_impuestos' => (int) $product->id_tax_rules_group,
            'stock' => $stock,
            'imagen' => $imagen,
        ]);
    }

    private function responder(array $data, int $code = 200)
    {
        header('Content-Type: application/json; charset=utf-8');
        http_response_code($code);
        die(json_encode($data, JSON_UNESCAPED_UNICODE));
    }
}
