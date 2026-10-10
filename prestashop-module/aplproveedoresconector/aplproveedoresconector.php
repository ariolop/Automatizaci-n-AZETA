<?php
/**
 * aplproveedoresconector — Módulo PrestaShop 9 para recibir productos del scraper de AZETA.
 *
 * Front controllers (autenticados por token):
 *   - controller=crear      : crea un producto DESACTIVADO a partir del JSON recibido.
 *   - controller=listado    : devuelve los productos (id, ean13, nombre, activo, precio PVP).
 *   - controller=comprobar  : comprueba qué EANs ya existen.
 *   - controller=opciones   : proveedores e impuestos disponibles.
 *   - controller=actualizar : actualiza EAN13/nombre de un producto (sincronización).
 *   - controller=ficha      : ficha resumida por EAN (nombre, precio, imagen, activo).
 *
 * EAN de expositor:
 *   Algunos productos se guardan en la tienda con un EAN distinto al que usa el
 *   proveedor (AZETA / Liderpapel) para el expositor. Ese segundo EAN vive en la
 *   tabla propia `<prefijo>apl_product_expositor` (id_product, ean_expositor,
 *   date_add, date_upd). Todos los endpoints lo devuelven como 'ean_expositor' y
 *   'comprobar'/'ficha' también localizan productos por él.
 *
 * Compatibilidad: PrestaShop 8.0 – 9.x (PHP 8.1+).
 */

if (!defined('_PS_VERSION_')) {
    exit;
}

class Aplproveedoresconector extends Module
{
    public function __construct()
    {
        $this->name = 'aplproveedoresconector';
        $this->tab = 'administration';
        $this->version = '1.6.0';
        $this->author = 'Clearis';
        $this->need_instance = 0;
        $this->ps_versions_compliancy = ['min' => '8.0.0', 'max' => '9.99.99'];
        $this->bootstrap = true;

        parent::__construct();

        $this->displayName = $this->l('APL Proveedores Connector');
        $this->description = $this->l('Crea, comprueba y actualiza productos desde la app de proveedores (AZETA, Liderpapel).');
        $this->confirmUninstall = $this->l('¿Seguro que quieres desinstalar APL Proveedores Connector?');
    }

    public function install()
    {
        if (Shop::isFeatureActive()) {
            Shop::setContext(Shop::CONTEXT_ALL);
        }
        // Genera un token aleatorio inicial
        Configuration::updateValue('APLPROVEEDORESCONECTOR_TOKEN', Tools::passwdGen(40));
        return parent::install();
    }

    public function uninstall()
    {
        Configuration::deleteByName('APLPROVEEDORESCONECTOR_TOKEN');
        return parent::uninstall();
    }

    /**
     * Página de configuración en el back office: ver/regenerar el token y ver las URLs.
     */
    public function getContent()
    {
        $output = '';

        if (Tools::isSubmit('submitToken')) {
            $token = trim(Tools::getValue('APLPROVEEDORESCONECTOR_TOKEN'));
            if ($token === '') {
                $token = Tools::passwdGen(40);
            }
            Configuration::updateValue('APLPROVEEDORESCONECTOR_TOKEN', $token);
            $output .= $this->displayConfirmation($this->l('Token guardado.'));
        }

        if (Tools::isSubmit('regenerarToken')) {
            Configuration::updateValue('APLPROVEEDORESCONECTOR_TOKEN', Tools::passwdGen(40));
            $output .= $this->displayConfirmation($this->l('Token regenerado.'));
        }

        $token = Configuration::get('APLPROVEEDORESCONECTOR_TOKEN');
        $base = Tools::getShopDomainSsl(true) . __PS_BASE_URI__;
        $urlCrear = $base . 'index.php?fc=module&module=aplproveedoresconector&controller=crear';
        $urlListado = $base . 'index.php?fc=module&module=aplproveedoresconector&controller=listado';

        $info = '<div class="panel"><h3>'.$this->l('Datos para tu app de proveedores (.env)').'</h3>'
            . '<p><b>PRESTASHOP_URL</b> = <code>'.rtrim($base, '/').'</code></p>'
            . '<p><b>PRESTASHOP_TOKEN</b> = <code>'.htmlspecialchars($token).'</code></p>'
            . '<hr><p class="text-muted">'.$this->l('Endpoints:').'</p>'
            . '<p>Crear: <code>'.htmlspecialchars($urlCrear).'</code></p>'
            . '<p>Listado: <code>'.htmlspecialchars($urlListado).'</code></p>'
            . '</div>';

        return $output . $info . $this->renderForm();
    }

    protected function renderForm()
    {
        $fields_form = [
            'form' => [
                'legend' => ['title' => $this->l('Configuración'), 'icon' => 'icon-cogs'],
                'input' => [
                    [
                        'type' => 'text',
                        'label' => $this->l('Token de autenticación'),
                        'name' => 'APLPROVEEDORESCONECTOR_TOKEN',
                        'desc' => $this->l('Debe coincidir con PRESTASHOP_TOKEN en el .env de tu app.'),
                        'size' => 50,
                    ],
                ],
                'submit' => ['title' => $this->l('Guardar'), 'name' => 'submitToken'],
            ],
        ];

        $helper = new HelperForm();
        $helper->module = $this;
        $helper->name_controller = $this->name;
        $helper->token = Tools::getAdminTokenLite('AdminModules');
        $helper->currentIndex = AdminController::$currentIndex . '&configure=' . $this->name;
        $helper->submit_action = 'submitToken';
        $helper->fields_value['APLPROVEEDORESCONECTOR_TOKEN'] = Configuration::get('APLPROVEEDORESCONECTOR_TOKEN');

        return $helper->generateForm([$fields_form]);
    }

    // ----------------------------------------------------------------- //
    // EAN de expositor (tabla propia `<prefijo>apl_product_expositor`)
    // ----------------------------------------------------------------- //

    /**
     * Nombre de la tabla del EAN de expositor, o false si no existe en esta tienda.
     * Así el módulo sigue funcionando aunque la tabla no esté creada.
     */
    public function tablaExpositor()
    {
        static $tabla = null;
        if ($tabla === null) {
            $nombre = _DB_PREFIX_ . 'apl_product_expositor';
            try {
                $filas = Db::getInstance()->executeS('SHOW TABLES LIKE "' . pSQL($nombre) . '"');
                $tabla = !empty($filas) ? $nombre : false;
            } catch (Exception $e) {
                $tabla = false;
            }
        }
        return $tabla;
    }

    /** EAN de expositor de un producto ('' si no tiene). */
    public function obtenerEanExpositor($idProduct)
    {
        $tabla = $this->tablaExpositor();
        if (!$tabla) {
            return '';
        }
        $v = Db::getInstance()->getValue(
            'SELECT ean_expositor FROM ' . $tabla . ' WHERE id_product = ' . (int) $idProduct
        );
        return $v !== false && $v !== null ? (string) $v : '';
    }

    /** id_product a partir de un EAN de expositor (0 si no hay ninguno). */
    public function productoPorEanExpositor($ean)
    {
        $tabla = $this->tablaExpositor();
        $ean = preg_replace('/\D/', '', (string) $ean);
        if (!$tabla || $ean === '') {
            return 0;
        }
        return (int) Db::getInstance()->getValue(
            'SELECT id_product FROM ' . $tabla . ' WHERE ean_expositor = "' . pSQL($ean) . '" LIMIT 1'
        );
    }

    /**
     * Guarda (o borra, si llega vacío) el EAN de expositor de un producto.
     * Devuelve false si la tabla no existe en esta tienda.
     */
    public function guardarEanExpositor($idProduct, $ean)
    {
        $tabla = $this->tablaExpositor();
        if (!$tabla) {
            return false;
        }
        $idProduct = (int) $idProduct;
        $ean = preg_replace('/\D/', '', (string) $ean);
        if ($ean === '') {
            return Db::getInstance()->execute('DELETE FROM ' . $tabla . ' WHERE id_product = ' . $idProduct);
        }
        $ahora = date('Y-m-d H:i:s');
        // Sin depender de una clave única: si ya hay fila, se actualiza.
        $existe = (int) Db::getInstance()->getValue(
            'SELECT COUNT(*) FROM ' . $tabla . ' WHERE id_product = ' . $idProduct
        );
        if ($existe) {
            return Db::getInstance()->execute(
                'UPDATE ' . $tabla . ' SET ean_expositor = "' . pSQL($ean) . '",'
                . ' date_upd = "' . pSQL($ahora) . '" WHERE id_product = ' . $idProduct
            );
        }
        return Db::getInstance()->execute(
            'INSERT INTO ' . $tabla . ' (id_product, ean_expositor, date_add, date_upd)'
            . ' VALUES (' . $idProduct . ', "' . pSQL($ean) . '", "' . pSQL($ahora) . '", "' . pSQL($ahora) . '")'
        );
    }
}
