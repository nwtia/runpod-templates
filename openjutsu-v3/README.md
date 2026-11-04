NWTIA - OpenJutsu V3

Snapshot prive de notre OpenJutsu V3 : Qwen 4B + ClipProj v3.1, descriptif Qwen 8B,
H3 Ref2VA INT8, DMAD 6 etapes avec le sampler du workflow, LoRA personnalisables,
caches V2/V3 et protection d'un objet au premier plan par selection et suivi SAM.

L'image installe les versions epinglees de ComfyUI, des extensions, de Node
24.11.1 et de PyTorch 2.13 CUDA 13.0, compile l'app et verifie l'import des nodes
sur CPU lors de sa construction. Le premier lancement telecharge et verifie
environ 41 Go de modeles. Les redemarrages reutilisent les poids verifies.
Les modeles, LoRA, sorties et caches restent dans /workspace/openjutsu.
Le code et les dependances sont dans /opt/nwtia/openjutsu-runtime, dans l'image.
Le template est autonome ; il ne depend pas du PC Windows.

Configuration : GPU NVIDIA 48 Go VRAM conseille, pilote 580 ou plus recent,
64 Go de RAM conseilles, disque conteneur 20 Go, volume persistant 160 Go monte
sur /workspace. Les GPU plus petits peuvent demander beaucoup plus d'offload.

ACCES DEPUIS LE MAC
Ajouter sa cle publique SSH au compte RunPod avant de deployer. Le template
utilise uniquement le port TCP 22. OpenJutsu, le proxy et ComfyUI restent locaux
au pod : aucune interface sans mot de passe n'est exposee sur une URL publique.
Dans Connect, reprendre la commande SSH over exposed TCP et ajouter :
  -L 3001:127.0.0.1:3000 -N
Puis ouvrir http://localhost:3001 sur le Mac. Pas de mot de passe OpenJutsu.

LoRA personnelles : les copier dans /workspace/openjutsu/models/loras puis
rafraichir les modeles. Le template installe le LoRA DMAD de vitesse, sans
embarquer ni publier la bibliotheque personnelle du PC.

Logs : /workspace/openjutsu/logs/{comfy,proxy,app,ssh}.log
En cas de crash CUDA, redemarrer le moteur avant une nouvelle generation.
Le recent echec CUDA local n'est pas prouve corrige : reserve VRAM portee a 3 Gio
comme essai de marge memoire, avec les autres contournements precedents conserves.

ETAT DE VALIDATION
Code et paquet verifies localement. Aucune generation cloud validee : aucun GPU
n'a ete loue pour creer ce template. Le temps et la reussite du premier demarrage
Linux devront etre verifies lors du premier deploiement. Les modes exigeant des
modeles supplementaires (From picture, ControlNet, certains outils Fix faces)
peuvent demander leurs poids additionnels via Welcome & models.
Aucune video, photo, historique de jobs, cle privee ou mot de passe inclus.
