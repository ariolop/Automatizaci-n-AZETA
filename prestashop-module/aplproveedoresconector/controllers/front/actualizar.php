<?php
/**
 * Front controller: actualizar
 * Actualiza campos de un producto existente (EAN13 y/o nombre) para mantener
 * sincronizada la información entre PrestaShop y la app (monitor unificado).
 *
 * Endpoint:
 *   {shop}/index.php?fc=module&module=aplproveedoresconector&controller=actualizar
 *
 * Cuerpo (JSON, POST):
 * {
 *   "token": "...",
 *   "id_product": 123,        // (o "ean": "8411..." para localizarlo por EAN)
 *   "producto": { "ean13": "8411...", "nombre": "Nuevo nombre" }
 * }
 */

if (!defined('_PS_VERSION_')) {
    exit;
}

class AplproveedoresconectorActualizarModuleFrontController extends ModuleFrontController
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

        // Autenticación por token
        if (!hash_equals((string) Configuration::get('APLPROVEEDORESCONECTOR_TOKEN'), (string) ($in['token'] ?? ''))) {
            $this->responder(['success' => false, 'error' => 'Token no autorizado'], 401);
        }

        $id = (int) ($in['id_product'] ?? 0);
        // Si no viene id, se puede localizar por EAN
        if (!$id && !empty($in['ean'])) {
            $ean = preg_replace('/\D/', '', (string) $in['ean']);
            $id = (int) Db::getInstance()->getValue(
                'SELECT id_product FROM ' . _DB_PREFIX_ . 'product WHERE ean13 = "' . pSQL($ean) . '"'
            );
        }
        if (!$id) {
            $this->responder(['success' => false, 'error' => 'Falta id_product o ean'], 400);
        }

        $product = new Product($id);
        if (!Validate::isLoadedObject($product)) {
            $this->responder(['success' => false, 'error' => 'Producto no encontrado'], 404);
        }

        $p = isset($in['producto']) && is_array($in['producto']) ? $in['producto'] : [];
        $cambios = [];

        if (array_key_exists('ean13', $p)) {
            $product->ean13 = preg_replace('/\D/', '', (string) $p['ean13']);
            $cambios[] = 'ean13';
        }
        if (array_key_exists('nombre', $p) && trim((string) $p['nombre']) !== '') {
            $nombre = $this->limpiar((string) $p['nombre'], 128);
            foreach (Language::getLanguages(true) as $l) {
                $product->name[(int) $l['id_lang']] = $nombre;
            }
            $cambios[] = 'nombre';
        }

        if (!$cambios) {
            $this->responder(['success' => false, 'error' => 'Nada que actualizar'], 400);
        }

        try {
            if (!$product->save()) {
                $this->responder(['success' => false, 'error' => 'No se pudo guardar'], 500);
            }
        } catch (Exception $e) {
            $this->responder(['success' => false, 'error' => $e->getMessage()], 500);
        }

        $this->responder([
            'success' => true,
            'id_product' => $id,
            'actualizado' => $cambios,
            'ean13' => $product->ean13,
        ]);
    }

    private function limpiar(string $txt, int $max): string
    {
        $txt = strip_tags($txt);
        if (mb_strlen($txt) > $max) {
            $txt = mb_substr($txt, 0, $max - 1) . '…';
        }
        return $txt;
    }

    private function responder(array $data, int $code = 200)
    {
        header('Content-Type: application/json; charset=utf-8');
        http_response_code($code);
        die(json_encode($data, JSON_UNESCAPED_UNICODE));
    }
}
