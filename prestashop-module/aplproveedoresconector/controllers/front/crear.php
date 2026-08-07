<?php
/**
 * Front controller: crear
 * Crea un producto a partir del JSON enviado por la app AZETA Manager. Por
 * defecto se crea DESACTIVADO; se puede crear activo con "activo": true, y fijar
 * el PVP (precio de venta CON IVA) con "precio_venta_con_iva".
 *
 * Endpoint:
 *   {shop}/index.php?fc=module&module=aplproveedoresconector&controller=crear
 *
 * Cuerpo (JSON, POST):
 * {
 *   "token": "...",
 *   "id_categoria": 2,
 *   "producto": {
 *      "nombre": "...", "ean13": "...", "precio_sin_iva": 4.95, "pvp": 7.19,
 *      "fabricante": "...", "descripcion": "...", "situacion": "...",
 *      "imagenes": ["https://...","https://..."],
 *      "stock": 12,          // opcional: cantidad inicial (StockAvailable)
 *      "modo_venta": 2       // opcional: 1=dropshipping, 2=tienda física, 3=ambos (módulo aplsalemode)
 *   }
 * }
 */

if (!defined('_PS_VERSION_')) {
    exit;
}

class AplproveedoresconectorCrearModuleFrontController extends ModuleFrontController
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
        $token = isset($in['token']) ? $in['token'] : '';
        if (!hash_equals((string) Configuration::get('APLPROVEEDORESCONECTOR_TOKEN'), (string) $token)) {
            $this->responder(['success' => false, 'error' => 'Token no autorizado'], 401);
        }

        $p = isset($in['producto']) && is_array($in['producto']) ? $in['producto'] : [];
        $nombre = trim((string) ($p['nombre'] ?? ''));
        $ean = preg_replace('/\D/', '', (string) ($p['ean13'] ?? ''));

        if ($nombre === '') {
            $this->responder(['success' => false, 'error' => 'Falta el nombre del producto'], 400);
        }

        // Evitar duplicados por EAN
        if ($ean !== '') {
            $existente = (int) Db::getInstance()->getValue(
                'SELECT id_product FROM ' . _DB_PREFIX_ . 'product WHERE ean13 = "' . pSQL($ean) . '"'
            );
            if ($existente) {
                $this->responder([
                    'success' => false,
                    'error' => 'Ya existe un producto con ese EAN',
                    'id_product' => $existente,
                ], 409);
            }
        }

        $idLang = (int) Configuration::get('PS_LANG_DEFAULT');
        $idCategoria = (int) ($in['id_categoria'] ?? Configuration::get('PS_HOME_CATEGORY'));
        if ($idCategoria <= 0) {
            $idCategoria = (int) Configuration::get('PS_HOME_CATEGORY');
        }
        $idProveedor = (int) ($in['id_proveedor'] ?? 0);
        $idImpuestos = (int) ($in['id_impuestos'] ?? 0);

        try {
            // Fabricante (crear si no existe)
            $idFabricante = 0;
            $fab = trim((string) ($p['fabricante'] ?? ''));
            if ($fab !== '') {
                $idFabricante = (int) Manufacturer::getIdByName($fab);
                if (!$idFabricante) {
                    $m = new Manufacturer();
                    $m->name = $fab;
                    $m->active = 1;
                    $m->add();
                    $idFabricante = (int) $m->id;
                }
            }

            $product = new Product();
            $product->name = [$idLang => $this->limpiar($nombre, 128)];
            $product->link_rewrite = [$idLang => Tools::str2url($nombre) ?: ('producto-' . $ean)];
            $descripcion = trim((string) ($p['descripcion'] ?? ''));
            if ($descripcion !== '') {
                $product->description = [$idLang => $descripcion];
                $product->description_short = [$idLang => $this->limpiar($descripcion, 400)];
            }
            // Coste del producto (precio de compra) = coste real con IVA + recargo de equivalencia
            $precioUnidad = (float) ($p['precio_sin_iva'] ?? 0);
            $costeReal = isset($p['coste_real']) ? (float) $p['coste_real'] : $precioUnidad;
            $product->wholesale_price = $costeReal;

            // Precio de venta (SIN IVA), que es lo que almacena PrestaShop.
            // Prioridad: 1) precio indicado a mano; 2) PVP = coste x MARGEN convertido
            // a base sin IVA; 3) PVP recomendado o precio sin IVA del proveedor.
            $MARGEN_PVP = 1.5;
            $tasaIva = $this->tasaImpuesto($idImpuestos);   // % (p. ej. 21.0)
            // Precio de venta introducido a mano: viene CON IVA (PVP). PrestaShop
            // guarda el precio base (sin IVA), así que lo convertimos con el tipo.
            $pvpConIvaManual = (isset($p['precio_venta_con_iva']) && $p['precio_venta_con_iva'] !== '' && $p['precio_venta_con_iva'] !== null)
                ? (float) $p['precio_venta_con_iva'] : null;
            if ($pvpConIvaManual !== null && $pvpConIvaManual > 0) {
                $product->price = $tasaIva > 0
                    ? round($pvpConIvaManual / (1 + $tasaIva / 100), 6)
                    : round($pvpConIvaManual, 6);
            } elseif ($costeReal > 0) {
                $pvpConIva = $costeReal * $MARGEN_PVP;
                $product->price = $tasaIva > 0
                    ? round($pvpConIva / (1 + $tasaIva / 100), 6)
                    : round($pvpConIva, 6);
            } else {
                // Sin coste: caemos al PVP recomendado o al precio sin IVA del proveedor.
                $pvp = isset($p['pvp']) ? (float) $p['pvp'] : null;
                $product->price = $pvp !== null && $pvp > 0 ? $pvp : $precioUnidad;
            }

            $product->id_category_default = $idCategoria;
            $product->ean13 = $ean;
            // Activo opcional: por defecto se crea DESACTIVADO; si llega activo=true
            // (o 1), se crea ya activado.
            $activo = filter_var($p['activo'] ?? false, FILTER_VALIDATE_BOOLEAN) ? 1 : 0;
            $product->active = $activo;
            // Si AZETA NO permite dropshipping, desactivamos "Disponible para pedido"
            $permiteDs = !isset($p['dropshipping']) || (bool) $p['dropshipping'];
            $product->available_for_order = $permiteDs ? 1 : 0;
            $product->product_type = 'standard';
            if ($idFabricante) {
                $product->id_manufacturer = $idFabricante;
            }
            // Regla de impuestos elegida en la configuración de la app
            if ($idImpuestos > 0) {
                $product->id_tax_rules_group = $idImpuestos;
            }
            // Proveedor por defecto
            if ($idProveedor > 0) {
                $product->id_supplier = $idProveedor;
            }

            if (!$product->add()) {
                $this->responder(['success' => false, 'error' => 'No se pudo crear el producto'], 500);
            }

            // Asociar a la categoría
            $product->addToCategories([$idCategoria]);

            // Asociar proveedor (tabla product_supplier + asociación de catálogo)
            if ($idProveedor > 0) {
                $this->asociarProveedor((int) $product->id, $idProveedor, $costeReal);
            }

            // Stock inicial (opcional). Si no viene, se deja el que ponga PrestaShop (0).
            $idShop = (int) $this->context->shop->id;
            $stockFijado = null;
            if (array_key_exists('stock', $p) && $p['stock'] !== null && $p['stock'] !== '') {
                $stockFijado = max(0, (int) $p['stock']);
                StockAvailable::setQuantity((int) $product->id, 0, $stockFijado, $idShop);
            }

            // Modo de venta (opcional): lo gestiona el módulo aplsalemode. Se aplica
            // DESPUÉS del stock, porque "tienda física" calcula la disponibilidad a
            // partir del stock. Es defensivo: si el módulo no está, no hace nada.
            $modoAplicado = null;
            if (array_key_exists('modo_venta', $p) && $p['modo_venta'] !== null && $p['modo_venta'] !== '') {
                $modoAplicado = $this->aplicarModoVenta((int) $product->id, $idShop, (int) $p['modo_venta']);
            }

            // Imágenes
            $imagenes = isset($p['imagenes']) && is_array($p['imagenes']) ? $p['imagenes'] : [];
            $imgOk = 0;
            $imgDiag = [];   // diagnóstico por imagen (para depurar por qué no suben)
            foreach ($imagenes as $i => $urlImg) {
                $motivo = $this->añadirImagen((int) $product->id, $urlImg, $imgOk === 0);
                if ($motivo === 'ok') {
                    $imgOk++;
                }
                $imgDiag[] = ['i' => $i, 'motivo' => $motivo];
            }

            $this->responder([
                'success' => true,
                'id_product' => (int) $product->id,
                'imagenes_recibidas' => count($imagenes),
                'imagenes_subidas' => $imgOk,
                'imagenes_diag' => $imgDiag,
                'activo' => (bool) $activo,
                'disponible_para_pedido' => (bool) $permiteDs,
                'stock' => $stockFijado,
                'modo_venta' => $modoAplicado,
            ]);
        } catch (Exception $e) {
            $this->responder(['success' => false, 'error' => $e->getMessage()], 500);
        }
    }

    /**
     * Aplica el "modo de venta" del producto delegando en el módulo aplsalemode
     * (tabla aplsalemode_product + sincronización de flags de stock). Defensivo:
     * si el módulo no está instalado/activo o falla la carga, no hace nada y
     * devuelve null. Devuelve el modo aplicado (int) si tiene éxito.
     *
     * Modos: 1 = dropshipping, 2 = tienda física, 3 = ambos. (0 = sin definir.)
     */
    private function aplicarModoVenta(int $idProduct, int $idShop, int $modo)
    {
        if ($idProduct <= 0 || $modo <= 0) {
            return null;
        }
        if (!Module::isInstalled('aplsalemode') || !Module::isEnabled('aplsalemode')) {
            return null;
        }
        // Cargar el módulo para que registre su autoloader PSR-4.
        $mod = Module::getInstanceByName('aplsalemode');
        if (!$mod) {
            return null;
        }
        $clase = 'PrestaShop\\Module\\Aplsalemode\\Service\\SaleModeManager';
        if (!class_exists($clase)) {
            return null;
        }
        try {
            $manager = new $clase();
            return $manager->applyMode($idProduct, $idShop, $modo) ? $modo : null;
        } catch (Exception $e) {
            return null;
        }
    }

    private function asociarProveedor(int $idProduct, int $idProveedor, float $coste): void
    {
        // Registro en product_supplier (precio de compra por proveedor)
        $existe = (int) ProductSupplier::getIdByProductAndSupplier($idProduct, 0, $idProveedor);
        if (!$existe) {
            $ps = new ProductSupplier();
            $ps->id_product = $idProduct;
            $ps->id_product_attribute = 0;
            $ps->id_supplier = $idProveedor;
            $ps->product_supplier_reference = '';
            $ps->product_supplier_price_te = $coste;
            $ps->id_currency = (int) Configuration::get('PS_CURRENCY_DEFAULT');
            $ps->save();
        }
        // Asociación de catálogo (tabla product_supplier ya cubre el filtro por proveedor;
        // además fijamos el proveedor por defecto en el producto, ya hecho con id_supplier).
    }

    /**
     * Añade una imagen al producto. Devuelve 'ok' si se sube, o un código de
     * motivo si se descarta (para poder depurar por qué no aparecen imágenes).
     */
    private function añadirImagen(int $idProduct, string $url, bool $cover): string
    {
        $url = trim($url);
        if ($url === '') {
            return 'vacia';
        }
        // Origen de la imagen: data URI en base64 (enviado por la app, p. ej.
        // Liderpapel tras Akamai) o URL http remota (p. ej. AZETA).
        if (stripos($url, 'data:') === 0) {
            $contenido = $this->decodificarDataUri($url);
        } elseif (stripos($url, 'http') === 0) {
            $contenido = Tools::file_get_contents($url);
        } else {
            return 'origen_no_soportado';
        }
        if ($contenido === false || strlen($contenido) < 100) {
            return 'contenido_invalido';
        }

        // Descargar a temporal y validar ANTES de crear la fila Image,
        // para no dejar filas huérfanas (con cover=1) si la imagen no es procesable.
        $tmp = tempnam(_PS_TMP_IMG_DIR_, 'azc');
        file_put_contents($tmp, $contenido);
        if (!ImageManager::isRealImage($tmp) || !@getimagesize($tmp)) {
            @unlink($tmp);
            return 'no_es_imagen_real';
        }

        $image = new Image();
        $image->id_product = $idProduct;
        $image->position = Image::getHighestPosition($idProduct) + 1;
        // Solo un producto puede tener cover=1; el resto debe ir a NULL, no 0.
        $image->cover = $cover ? true : null;
        if (!$image->add()) {
            @unlink($tmp);
            return 'add_fallo';
        }

        $destino = $image->getPathForCreation();
        if (!ImageManager::resize($tmp, $destino . '.jpg')) {
            @unlink($tmp);
            // Borrar la fila recién creada para no dejar un cover huérfano
            // que haga colisionar la siguiente imagen (Duplicate entry id_product_cover).
            $image->delete();
            return 'resize_fallo';
        }
        $tiposImagen = ImageType::getImagesTypes('products');
        foreach ($tiposImagen as $t) {
            ImageManager::resize(
                $tmp,
                $destino . '-' . stripslashes($t['name']) . '.jpg',
                (int) $t['width'],
                (int) $t['height']
            );
        }
        @unlink($tmp);
        return 'ok';
    }

    /**
     * Decodifica un data URI ("data:image/jpeg;base64,....") y devuelve el
     * binario, o false si no es válido.
     */
    private function decodificarDataUri(string $uri)
    {
        $coma = strpos($uri, ',');
        if ($coma === false) {
            return false;
        }
        $meta = substr($uri, 0, $coma);
        $datos = substr($uri, $coma + 1);
        if (stripos($meta, 'base64') !== false) {
            $bin = base64_decode($datos, true);
            return $bin === false ? false : $bin;
        }
        return rawurldecode($datos);
    }

    /**
     * Tipo de IVA (%) de una regla de impuestos, usando el país por defecto de
     * la tienda. Devuelve 0.0 si no se puede determinar (sin impuestos).
     */
    private function tasaImpuesto(int $idTaxRulesGroup): float
    {
        if ($idTaxRulesGroup <= 0) {
            return 0.0;
        }
        try {
            $address = new Address();
            $address->id_country = (int) Configuration::get('PS_COUNTRY_DEFAULT');
            $tm = TaxManagerFactory::getManager($address, $idTaxRulesGroup);
            return (float) $tm->getTaxCalculator()->getTotalRate();
        } catch (Exception $e) {
            return 0.0;
        }
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
