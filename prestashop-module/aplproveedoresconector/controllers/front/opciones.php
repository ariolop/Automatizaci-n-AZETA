<?php
/**
 * Front controller: opciones
 * Devuelve listas auxiliares de la tienda para la pestaña Configuración de la app:
 *   - proveedores  (id, nombre)
 *   - impuestos    (reglas de impuestos: id, nombre)
 *
 * Endpoint:
 *   {shop}/index.php?fc=module&module=aplproveedoresconector&controller=opciones
 * Cuerpo (JSON, POST):  { "token": "..." }
 */

if (!defined('_PS_VERSION_')) {
    exit;
}

class AplproveedoresconectorOpcionesModuleFrontController extends ModuleFrontController
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

        // Proveedores
        $proveedores = [];
        foreach (Supplier::getSuppliers(false, $idLang, true) as $s) {
            $proveedores[] = [
                'id' => (int) $s['id_supplier'],
                'nombre' => $s['name'],
            ];
        }

        // Reglas de impuestos
        $impuestos = [];
        foreach (TaxRulesGroup::getTaxRulesGroups(true) as $t) {
            $impuestos[] = [
                'id' => (int) $t['id_tax_rules_group'],
                'nombre' => $t['name'],
            ];
        }

        $this->responder([
            'success' => true,
            'proveedores' => $proveedores,
            'impuestos' => $impuestos,
        ]);
    }

    private function responder(array $data, int $code = 200)
    {
        header('Content-Type: application/json; charset=utf-8');
        http_response_code($code);
        die(json_encode($data, JSON_UNESCAPED_UNICODE));
    }
}
