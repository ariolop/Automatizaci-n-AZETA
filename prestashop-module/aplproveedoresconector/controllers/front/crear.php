<?php
/**
 * Front controller: crear
 * Crea un producto DESACTIVADO a partir del JSON enviado por la app AZETA Manager.
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
 *      "imagenes": ["https://...","https://..."]
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
            // Precio de venta base (sin IVA): usamos el PVP recomendado si viene; si no, el coste.
            $pvp = isset($p['pvp']) ? (float) $p['pvp'] : null;
            $precioUnidad = (float) ($p['precio_sin_iva'] ?? 0);
            $product->price = $pvp !== null && $pvp > 0 ? $pvp : $precioUnidad;
            // Coste del producto (precio de compra) = coste real con IVA + recargo de equivalencia
            $costeReal = isset($p['coste_real']) ? (float) $p['coste_real'] : $precioUnidad;
            $product->wholesale_price = $costeReal;

            $product->id_category_default = $idCategoria;
            $product->ean13 = $ean;
            $product->active = 0;            // <<< DESACTIVADO
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
                'activo' => false,
                'disponible_para_pedido' => (bool) $permiteDs,
            ]);
        } catch (Exception $e) {
            $this->responder(['success' => false, 'error' => $e->getMessage()], 500);
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
