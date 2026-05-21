# Conversor-video (Versão 6)

Aplicativo desktop em Python + CustomTkinter para converter vídeos para WhatsApp em Windows 10/11.

## Recursos da versão 6

- Fila de múltiplos arquivos
- Modo **quality** (CRF + preset de resolução)
- Modo **target_size** (alvo em MB por item)
- Seleção global de pasta de saída
- Retry para itens com status **Erro** ou **Cancelado**
- Estimativa de tamanho por item na fila
- Thumbnail/preview do item selecionado (com fallback amigável)
- Cancelar item atual / parar após item atual
- Logs na interface

## Estrutura esperada

```text
.
├─ app.py
├─ ConversorWhatsApp.spec
├─ ffmpeg/
│  ├─ ffmpeg.exe
│  └─ ffprobe.exe
└─ output/   (criada automaticamente)
```

## Executar em desenvolvimento

```bash
pip install customtkinter
python app.py
```

## Gerar executável portátil (PyInstaller)

```bash
pip install pyinstaller
pyinstaller ConversorWhatsApp.spec
```

No build `onedir`, `ffmpeg.exe` e `ffprobe.exe` são incluídos ao lado do executável.
