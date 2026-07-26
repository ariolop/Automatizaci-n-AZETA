<?php
/**
 * azetaconnector — Módulo PrestaShop 9 para recibir productos del scraper de AZETA.
 *
 * Expone dos front controllers (autenticados por token):
 *   - controller=crear    : crea un producto DESACTIVADO a partir del JSON recibido.
 *   - controller=listado  : devuelve los productos (id, ean13, nombre, activo).
 *
 * Compatibilidad: PrestaShop 8.0 – 9.x (PHP 8.1+).
 */

if (!defined('_PS_VERSION_')) {
    exit;
}

class AzetaConnector extends Module
{
    public function __construct()
    {
        $this->name = 'azetaconnector';
        $this->tab = 'administration';
        $this->version = '1.0.0';
        $this->author = 'Clearis';
        $this->need_instance = 0;
        $this->ps_versions_compliancy = ['min' => '8.0.0', 'max' => '9.99.99'];
        $this->bootstrap = true;

        parent::__construct();

        $this->displayName = $this->l('AZETA Connector');
        $this->description = $this->l('Recibe productos del scraper de AZETA y los crea desactivados.');
        $this->confirmUninstall = $this->l('¿Seguro que quieres desinstalar AZETA Connector?');
    }

    public function install()
    {
        if (Shop::isFeatureActive()) {
            Shop::setContext(Shop::CONTEXT_ALL);
        }
        // Genera un token aleatorio inicial
        Configuration::updateValue('AZETACONNECTOR_TOKEN', Tools::passwdGen(40));
        return parent::install();
    }

    public function uninstall()
    {
        Configuration::deleteByName('AZETACONNECTOR_TOKEN');
        return parent::uninstall();
    }

    /**
     * Página de configuración en el back office: ver/regenerar el token y ver las URLs.
     */
    public function getContent()
    {
        $output = '';

        if (Tools::isSubmit('submitAzetaToken')) {
            $token = trim(Tools::getValue('AZETACONNECTOR_TOKEN'));
            if ($token === '') {
                $token = Tools::passwdGen(40);
            }
            Configuration::updateValue('AZETACONNECTOR_TOKEN', $token);
            $output .= $this->displayConfirmation($this->l('Token guardado.'));
        }

        if (Tools::isSubmit('regenerarToken')) {
            Configuration::updateValue('AZETACONNECTOR_TOKEN', Tools::passwdGen(40));
            $output .= $this->displayConfirmation($this->l('Token regenerado.'));
        }

        $token = Configuration::get('AZETACONNECTOR_TOKEN');
        $base = Tools::getShopDomainSsl(true) . __PS_BASE_URI__;
        $urlCrear = $base . 'index.php?fc=module&module=azetaconnector&controller=crear';
        $urlListado = $base . 'index.php?fc=module&module=azetaconnector&controller=listado';

        $info = '<div class="panel"><h3>'.$this->l('Datos para tu app AZETA Manager (.env)').'</h3>'
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
                        'name' => 'AZETACONNECTOR_TOKEN',
                        'desc' => $this->l('Debe coincidir con PRESTASHOP_TOKEN en el .env de tu app.'),
                        'size' => 50,
                    ],
                ],
                'submit' => ['title' => $this->l('Guardar'), 'name' => 'submitAzetaToken'],
            ],
        ];

        $helper = new HelperForm();
        $helper->module = $this;
        $helper->name_controller = $this->name;
        $helper->token = Tools::getAdminTokenLite('AdminModules');
        $helper->currentIndex = AdminController::$currentIndex . '&configure=' . $this->name;
        $helper->submit_action = 'submitAzetaToken';
        $helper->fields_value['AZETACONNECTOR_TOKEN'] = Configuration::get('AZETACONNECTOR_TOKEN');

        return $helper->generateForm([$fields_form]);
    }
}
