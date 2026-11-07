# import block

import cv2
import numpy as np
import PIL.Image as Image
from PIL import ImageOps
import matplotlib.pyplot as plt
import os
import pandas as pd
import pillow_heif
from scipy.sparse import csc_matrix
from numba import njit, prange
import piexif
import time
from image_io import read_image


# tmp params block, to be config
h = 800 # height for resize to
w = 600 # width for resize to
kernel = 6
desired_stride = 2
threshold = 9
#path = "c:/Users/AlexSmart/Desktop/imgs/"
#path = "E:/pedastrian/"
#path = "E:/photo iphone 13/202205__"
extensions = [".jpg", ".jpeg", ".heic", ".png"]

# internal progress
progress = 0

###



def get_extension(file_path):
    ans = os.path.splitext(file_path)[-1].lower()
    return ans


# function for tune stride for resolution and kernel
def correct_stride(img = False, kernel_size=5, desired_stride=1, h = h, w = w):
    
    if type(img) != bool:
        #print(f"cs input shape {img.shape}")
        h = img.shape[0]
        w = img.shape[1]
    else:
        h=h
        w=w

    casted_h_steps = (h-kernel_size)//desired_stride
    casted_w_steps = (w-kernel_size)//desired_stride

    desired_h_steps = (h-kernel_size)/desired_stride
    desired_w_steps = (w-kernel_size)/desired_stride

    valid_h_steps = np.equal(casted_h_steps, desired_h_steps) 
    valid_w_steps = np.equal(casted_w_steps, desired_w_steps)

    if (valid_h_steps and valid_w_steps):
        #print(f"stride set to initial: {desired_stride}")
        #print(f"result dim is: {(h-kernel_size)/desired_stride, (w-kernel_size)/desired_stride}")
        return desired_stride

    else:
        for stride in range(desired_stride, 0, -1):
            equal_h_stride = np.equal((h - kernel_size) // stride, (h - kernel_size) / stride)
            equal_w_stride = np.equal((w - kernel_size) // stride, (w - kernel_size) / stride)
            if equal_h_stride and equal_w_stride:
                
                #print(f"found best stride: {stride}")
                #print(f"result dim is: {(h-kernel_size)/stride, (w-kernel_size)/stride}")
                return stride
        # Если не найдено, возвращаем минимальный страйд
        return 1
"""    
# main pooling function (obsolete, now njit in use)
def border_pooling(img, kernel_size=5, stride=5, threshold=threshold):
    h, w= img.shape

    # Определяем количество "шагов" по вертикали и горизонтали
    output_h = (h - kernel_size)//stride  # скоректировать на страйд
    output_w = (w-kernel_size)//stride # скоректировать на страйд

    # Используем numpy strides для получения "вырезанных" окон с заданным stride
    shape = (output_h, output_w, kernel_size, kernel_size)
    #print(f"shape = {shape} type of {type(shape)}")
    strides = (
        img.strides[0] * stride,
        img.strides[1] * stride,
        img.strides[0],
        img.strides[1]
    )

    #print(f"strides = {strides} type of {type(strides)}")


        # Преобразуем изображение в 4D массив, используя шаг и kernel
    img_reshaped = np.lib.stride_tricks.as_strided(img, shape=shape, strides=strides)

        # Вычисляем максимальные и минимальные значения в каждом блоке kernel
    max_vals = img_reshaped.max(axis=(2, 3), keepdims=False)
    min_vals = img_reshaped.min(axis=(2, 3), keepdims=False)

        # Вычисляем разницу и применяем порог
    condition = (max_vals - min_vals) > threshold
    #print(f"condition*1 dtype {(condition*1).dtype}")

    return (condition*1).astype(np.int16)
"""

# main pooling function optimized by njit
@njit(parallel = True)
def border_pooling_numba(img, kernel_size=6, stride=2, threshold=threshold):
    """ 
    fast pooling realization
    """

    h, w= img.shape
    
    # Определяем количество "шагов" по вертикали и горизонтали
    output_h = (h - kernel_size)//stride  # скоректировать на страйд
    output_w = (w-kernel_size)//stride # скоректировать на страйд

    res = np.zeros((output_h, output_w))
    #mins = np.zeros((output_h, output_w))
    
    # Основной цикл с использованием numba
    for i in prange(output_h):
        for j in range(output_w):
            # Инициализация минимального и максимального значений
            min_val = 300
            max_val = -300

            block = img[i*stride:i*stride + kernel_size, j*stride:j*stride + kernel_size]
            b_h, b_w = block.shape
            #print(f"block.shape {block.shape}")
            # Внутренние циклы по блоку
            for k in range(b_h):
                for l in range(b_w):
                    val = block[k,l]
                    if val > max_val:
                        max_val = val
                    if val < min_val:
                        min_val = val
                    
            if max_val - min_val >threshold:
                res[i,j] = 1
    
    return res

# augmentation function. full augs cut to rotate 180 only
def gen_augs(img):
    rot = cv2.rotate(img, cv2.ROTATE_180)
    #xflip = cv2.flip(img, 0)
    #xrot = cv2.rotate(xflip, cv2.ROTATE_180)
    #yflip = cv2.flip(img, 1)
    #yrot = cv2.rotate(yflip, cv2.ROTATE_180)
    #xyflip = cv2.flip(img, -1)
    #xyrot = cv2.rotate(xyflip, cv2.ROTATE_180)

    return img, rot#, xflip, xrot, yflip, yrot, xyflip, xyrot


# heic opener
def open_heic(path):
    #pillow_heif.register_heif_opener()

    # Открыть HEIC изображение через PIL
    try:
           
        img = pillow_heif.read_heif(path)
        img = Image.frombytes(img.mode, img.size, img.data)
        img = np.array(img)
        img = img[:,:,::-1]
            
        # Показать изображение (или сохранить)
        #image.show()  # Показывает изображение через стандартный вьювер
        # image.save('output.png')  # Сохранить изображение в другом формате

    except Exception as e:
        print(f"Ошибка: {e}")

    return img


# main class
class Img():
    # no pillow realization
    # made LAB space only
    # for LAB threshold ~ 9, similarity > 0.8

    
    
    def __init__(self, path, color_space = "LAB", threshold = threshold, height = h, width = w,
                 border_compression = False, desired_stride = desired_stride, 
                 kernel = kernel, use_float16 = True, channels=("L", "a", "b"),
                 l_threshold=None):
        if color_space != "LAB":
            raise ValueError("Only LAB descriptors are supported")
        self.channels = tuple(channels)
        if (not self.channels or len(set(self.channels)) != len(self.channels)
                or any(channel not in ("L", "a", "b") for channel in self.channels)):
            raise ValueError("channels must contain distinct names from L, a, b")
        if kernel < 1 or desired_stride < 1 or min(height, width) <= kernel:
            raise ValueError("Use positive kernel/stride and dimensions larger than kernel")
        self.l_threshold = threshold if l_threshold is None else l_threshold
        if not (0 <= threshold <= 255 and 0 <= self.l_threshold <= 255):
            raise ValueError("LAB pooling thresholds must be between 0 and 255")
        self.path = path
        self.color_space = color_space
        self.border_compression = border_compression
        self.threshold = threshold
        self.use_float16 = use_float16
        #print(f"self threshold = {self.threshold}")
        self.kernel = kernel
        #print(f"self kernel = {self.kernel}")
        self.h = height
        self.w = width
        self.image = self.open_and_resize()
        #self.shape = self.image.shape[0:-1]
        #print(f"self.shape is {self.shape}")
        

        self.stride = correct_stride(self.image[:,:,0], kernel_size= self.kernel, desired_stride=desired_stride)
        #print(f"self stride is {self.stride}")
        self.hashes = self.layer_hash()

        
    #print("start open resize")
    def open_and_resize(self):
        
        im = read_image(self.path)

        if self.color_space == "RGB":
            # now not in use!!! not tested!!
            #real space in CV2 is BGR !!!
            pass
        if self.color_space == "LAB":
            im = cv2.cvtColor(im, cv2.COLOR_BGR2Lab)
        if self.color_space == "HSV":
            #not in use!! not tested!!!
            im = cv2.cvtColor(im, cv2.COLOR_RGB2HSV)
        
        if (im.shape[0]<im.shape[1]):
            im = cv2.rotate(im, cv2.ROTATE_90_CLOCKWISE)
        im = cv2.resize(im, dsize=(self.w, self.h))  # initial 270 x 400
        if self.use_float16:
            im = im.astype(np.float16)
        return im

    def layer_hash(self):
        # made only for LAB
        # проходим по каждому слою. определяем по порогу границы, выпрямляем, 
        # добавляем вектор инвертированный 

        layers = [{"L": 0, "a": 1, "b": 2}[channel] for channel in self.channels]
        aug_imgs = gen_augs(self.image)
        
        """
        todo! change vstack and hstack to array defenition and insertion
        """
        res = []
        counter = 0
        for layer in layers:
            layer_threshold = self.l_threshold if layer == 0 else self.threshold
            #print(f"start pooling layer {counter}")
            hash = border_pooling_numba(aug_imgs[0][:,:,layer].astype(np.int16), threshold=layer_threshold, kernel_size=self.kernel, stride=self.stride).flatten()
            #hash = np.append(hash, np.where(hash == 0, 1,0))
            for im in aug_imgs[1:]:
                im_hash = border_pooling_numba(im[:,:,layer].astype(np.int16), threshold=layer_threshold, kernel_size=self.kernel, stride=self.stride).flatten()
                #im_hash = np.append(im_hash, np.where(im_hash == 0, 1,0))
                hash = np.vstack((hash, im_hash))

                
            #print(f"hash shape = {hash.shape}")
            res.append(hash)
            counter +=1

        res = np.hstack(res)    
        return res
         
    #obslete function  
    def show(self):
        plt.imshow(self.image)

    #obsolete function
    def show_layer(self, layer_no):
        plt.imshow(self.image[:,:,layer_no])

def preview(path):
    return read_image(path)

# paths from list of folder path 
def read_paths(root_folder_path, read_subfolder = True):
    print(f"start read paths is {root_folder_path} type of {type(root_folder_path)}")
    if isinstance(root_folder_path, str):
        root_folder_path = [root_folder_path]
        #print()
    elif isinstance(root_folder_path, list):
        pass
    else:
        print(f"incorrect path type: {type(root_folder_path)}")
        raise ("path type error")
         
    
    file_paths = []
    for fold in root_folder_path:
        print(f"folder {fold}")
        
        for root, dirs, files in os.walk(fold):
            for file in files:
                print(root, file)
                file_paths.append(os.path.join(root, file))
            if not read_subfolder:
                break

    ans = set(file_paths)
    ans = list(ans)
    return ans

#read_paths(['C:/Users/AlexSmart/Desktop/imgs'])

# function that check if we have incorrect/unsupported extensions in paths
def check_extensions(path, extensions):
    """
    returns tuple: list of paths of correct files and set of new extensions
    """
    print(f"send root paths {path} to creation list of imgs paths")
    files = read_paths(path)
    print("start check ext")
    new_files = []
    correct_files = []
    for file in files:
        ext = os.path.splitext(file)[-1].lower()
        if ext not in extensions:
            new_files.append(file)
        else:
            correct_files.append(file)

    new_ext = set([os.path.splitext(item)[-1].lower() for item in new_files])
   
    if (len(new_files) != 0):
        print(f"found {len(new_files)} unexpected extensions, unique: {new_ext}")

    return (correct_files, new_ext)


# set and read correct paths
#correct_files, another = check_extensions(path=path, extensions=extensions)
#correct_files

# get short filename from path
def get_short_name(full_path):
    return full_path.split("/")[-1]

def create_hash_matrix(files_path_list, threshold, kernel, stride, sparse = True,
                            use_float16 = True, channels=("L", "a", "b"),
                            l_threshold=None, height=h, width=w):
    
    """
    creates matrix of hashes and returns np.array
    """

    global progress

    files = files_path_list
    if not files:
        return np.empty((0, 0)), np.empty((0, 0))
    hash_size = Img(files_path_list[0], threshold=threshold, kernel=kernel, 
                    desired_stride=stride, use_float16=use_float16, channels=channels,
                    l_threshold=l_threshold, height=height, width=width).hashes.shape[1]
    filenames = []
    matrix_hashes = np.zeros((len(files)*2, hash_size))  # hash_size - размер хеша
    for i, img in enumerate(files):
        #print(f"work on {img}")
        matrix_hashes[i*2:i*2+2] = Img(
            img, threshold=threshold, kernel=kernel, desired_stride=stride,
            use_float16=use_float16, channels=channels, l_threshold=l_threshold,
            height=height, width=width).hashes
        filenames.append(img)
        if i % 20 == 0:
            print(f"Processed {i + 1} of {len(files)}")
            progress = int(i/len(files)*100)
            print(f"module progress {progress, i, len(files)}")
    
    progress = 100
        
     
    mean_pixels = np.sum(matrix_hashes, axis=1)[::2]
    mean_pixels = (mean_pixels[:, None]+mean_pixels)/2

    return matrix_hashes, mean_pixels


def calc_similarity(hash_matrix, list_of_path, n_augs = 2, sparse = True):
        
    print(f"start multiplying")
    filenames = list_of_path
    if sparse:
        hash_matrix = csc_matrix(hash_matrix)

        res = pd.DataFrame(hash_matrix.dot(hash_matrix[::n_augs,:].T).toarray())

    else:
        res = pd.DataFrame(np.matmul(hash_matrix, hash_matrix[::n_augs,:].T))
        
      
    res["group"] = (res.index // n_augs) #от трансформации получается n_augs хешей пока оставили 2. Можно забрать в параметр потом
    res = res.groupby(res.group).agg("max")
    res.columns = [item for item in filenames]
    print("Comparsion finished.")
    
    return res

def clean_result_df(result_df, mean_pixels, similarity = 0.8):
    """
    cleaning comparsion result
    input - pandas.DF shape (n_imgs,n_imgs)
    returns pandas df columns: path1, path2, similarity %
    """

    print("Cleaing result.")
    res = result_df.div(mean_pixels).fillna(0)
    #res[res == 1] =0
    res[res < similarity] = 0
    res["names"] = result_df.columns
    res = res.set_index("names")
        
    #clean zeros
    res = res.loc[res.sum(axis=1) != 0]
    res = res.loc[:, res.sum(axis=0) != 0]

    # Преобразуем DataFrame в массив numpy
    arr = res.values

    # Используем np.triu для обнуления элементов ниже главной диагонали
    arr_lower = np.tril(arr)
    

    # Преобразуем обратно в DataFrame
    res = pd.DataFrame(arr_lower, columns=res.columns, index=res.index)
    res = res.stack().reset_index()
    res = res[res.iloc[:,0]!=res.iloc[:,1]]
    res.columns = ["row", "col", "val"]
    
    res = res[res.val != 0]
    res["val"] = (res["val"]*100).astype(int)
    #print(res)
    order = ["row", "val", "col"]
    return res.loc[:,order]


def get_progress():
    global progress
    return progress

def reset_progress():
    global progress
    progress = 0
    
    return 0

def open_and_extract_exif(path_to_image):
    pillow_heif.register_heif_opener()

    # Открываем HEIC изображение
    image = Image.open(path_to_image)
    
    # Получаем информацию EXIF из любого типа файла
    exif_info = image.info.get('exif', None)
    #print(f"exif info {exif_info}")
    
    if exif_info is not None:
        print(f"EXIF data found for {path_to_image}:")
        #print(f"type of Exif {type(exif_info)}")
        if isinstance(exif_info, bytes):
            conv = piexif.load(exif_info)
            #print(f"byte converted to {conv}")
            #print(type(conv))
            return conv
        else:
            print("returnin exif wo conversion")
            return exif_info
        
    else:
        print(f"No EXIF data found for {path_to_image}.")
        return None
    
def parse_exif_dict(exif_info):
    """
    now works with heic exif
    need exapmle of another files
    """
    if exif_info is not None:
        ans = {}
        selected_tags = {"0th":[272, 306], "Exif":[40962, 40963]}
        for key, values in selected_tags.items():
            for value in values:
                print(key, value)
                #print(exif_info)
                tag_name = piexif.TAGS[key].get(value, {}).get("name", f"Unknown tag {key}")
                val = exif_info[key][value]
                if type(val) == "byte":
                    val = val.decode("utf-8")
                
                ans[tag_name] = val

                
    else:
        ans = None

    return ans

def exif_dict_to_text(exif_dict):
    text = "EXIF: \n"

    if exif_dict is not None:
        exif_dict["Model"] = ["Камера", exif_dict["Model"]]
        exif_dict["DateTime"] = ["Дата создания", exif_dict["DateTime"]]
        exif_dict["Resolution"] = ["Разрешение", f"{exif_dict["PixelXDimension"]} x {exif_dict["PixelYDimension"]}"]
        exif_dict.pop("PixelXDimension", None)
        exif_dict.pop("PixelYDimension", None)
        for key in exif_dict.keys():
            if isinstance(exif_dict[key][1], bytes):
                exif_dict[key][1] = exif_dict[key][1].decode("utf-8")
            text = text + f"{exif_dict[key][0]}: {exif_dict[key][1]}\n"
    else:
        text = text + "Not found \n"
    
    return text

def get_exif_dict_to_text(path_to_image):
    exif = open_and_extract_exif(path_to_image)
    exif = parse_exif_dict(exif)
    exif = exif_dict_to_text(exif)
    return exif

def get_os_meta(file_path):
    # Получаем время создания
    creation_time = os.path.getctime(file_path)
    creation_time_readable = time.ctime(creation_time)
    # Получаем время последнего изменения
    modification_time = os.path.getmtime(file_path)
    modification_time_readable = time.ctime(modification_time)
    # время последнего доступа
    access_time = os.path.getatime(file_path)
    access_time_readable = time.ctime(access_time)
    # размер файла
    disk_size = os.path.getsize(file_path)
    if disk_size // (1024 * 1024 *1024) >0:
        readble_size = f"{round(disk_size / (1024 * 1024 *1024), 2)} Гб"
    elif disk_size // (1024 * 1024) >0:
        readble_size = f"{round(disk_size / (1024 * 1024 ), 2)} Мб"
    elif disk_size // (1024 ) >0:
        readble_size = f"{round(disk_size / (1024 ), 2)} Кб"
    else:
        readble_size = f"{disk_size} б"

    pillow_heif.register_heif_opener()
    width, heigh = Image.open(file_path).size
    
    return {"created":["Дата создания",creation_time_readable], "modified":["Дата Изменения",modification_time_readable], 
            "access":["Доступ", access_time_readable], "size":["Размер", readble_size], 
            "width":["Ширина",width], "heigh":["Высота",heigh]}

def os_meta_to_text(meta_dict):
    text = "Metadata:  \n"
    text = text + f"Разрешение: {meta_dict["width"][1]} x {meta_dict["heigh"][1]} \n"
    keys = ["size", "created", "modified", "access"]
    for key in keys:
        text = text + f"{meta_dict[key][0]}: {meta_dict[key][1]} \n"
    
    return text

  

def pipeline(paths, pipeline_extensions = extensions,similarity_threshold=0.8, pooling_threshold = threshold, kernel=kernel, 
                               stride=desired_stride, sparse = True, use_float16 = True,
                               channels=("L", "a", "b"), l_threshold=None, height=h, width=w):
    
    global progress

    

    if len(paths) == 0:
        print("empty path list")
        
        return pd.DataFrame(columns=["row", "val", "col"])
    files_list, new_ext = check_extensions(paths, extensions=pipeline_extensions)
    if not files_list:
        progress = 100
        return pd.DataFrame(columns=["row", "val", "col"])
    hashes, mean_pixels = create_hash_matrix(files_list, threshold=pooling_threshold, kernel=kernel,
                                             stride=stride, sparse=sparse, use_float16=use_float16,
                                             channels=channels, l_threshold=l_threshold,
                                             height=height, width=width)
    sim_df = calc_similarity(hashes, files_list, sparse=sparse)
    sim_df = clean_result_df(sim_df, mean_pixels=mean_pixels, similarity=similarity_threshold)

    #print(sim_df)
    return sim_df



#pipeline(path)

#path = "C:/Users/AlexSmart/Desktop/imgs/trans.jpeg"
#path = "C:/Users/AlexSmart/Desktop/imgs/IMG_5291.HEIC"
#path = "C:/Users/AlexSmart/Desktop/imgs/IMG_7380.HEIC"



