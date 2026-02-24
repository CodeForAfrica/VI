# Define variables
PYTHON_PKG_DIR = layer_build/python/lib/python3.11/site-packages
LAYER_ZIP = terraform/lambda_layer.zip

.PHONY: build_layer clean

build_layer:
	@echo "--- Cleaning old build artifacts ---"
	rm -rf layer_build
	mkdir -p $(PYTHON_PKG_DIR)
	
	@echo "--- Installing dependencies ---"
	# We use --only-binary=:all: to ensure we get the smaller, pre-compiled wheels
	pip install pandas numpy scikit-learn trafilatura torch -t $(PYTHON_PKG_DIR) \
		--only-binary=:all: --platform manylinux2014_x86_64
	
	@echo "--- Pruning unnecessary files to reduce size ---"
	find layer_build/ -type d -name "tests" -exec rm -rf {} +
	find layer_build/ -type d -name "__pycache__" -exec rm -rf {} +
	find layer_build/ -type d -name "*.dist-info" -exec rm -rf {} +
	find layer_build/ -name "*.so" -exec strip {} \;
	
	@echo "--- Zipping the layer ---"
	cd layer_build && zip -r9 ../$(LAYER_ZIP) .
	@echo "Successfully created $(LAYER_ZIP)"

clean:
	rm -rf layer_build
	rm -f $(LAYER_ZIP)
